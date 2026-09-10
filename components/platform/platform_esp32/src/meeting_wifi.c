#include "platform/meeting_wifi.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "nvs.h"
#include "cJSON.h"
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define LINK_UP BIT0
#define LINK_FAILED BIT1
#define DNS_DONE BIT2
#define DNS_READY BIT3
typedef struct { uint32_t version; char ssid[33]; char password[65]; } wifi_credentials_t;
static wifi_credentials_t saved, pending;
static SemaphoreHandle_t config_lock;
static EventGroupHandle_t events;
static httpd_handle_t server;
static esp_netif_t *ap_netif;
static atomic_bool linked, has_saved, portal, connecting, ignore_disconnect;
static atomic_bool setup_requested, candidate_ready, busy, dns_running;
static atomic_bool setup_complete;
static atomic_bool dns_advertised;
static atomic_uint dns_queries, redirects, page_views;
static atomic_int last_reason, setup_state;
static _Atomic(const char *) setup_message = "连接设备热点后打开配网页";
static bool started;
static int64_t close_at;
static char ap_name[24], csrf[33];
static const char portal_url[] = "http://192.168.4.1/";
extern const char portal_html_start[] asm("_binary_meeting_wifi_html_start");

// Phone connectivity probes must use this AP's DNS, not a stale or public DNS.
// Configure DHCP while the AP is stopped so the first lease contains option 6.
static bool configure_portal_dhcp(void) {
    atomic_store(&dns_advertised, false);
    esp_err_t error = esp_netif_dhcps_stop(ap_netif);
    if (error != ESP_OK && error != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STOPPED) return false;
    esp_netif_ip_info_t ip;
    if (esp_netif_get_ip_info(ap_netif, &ip) != ESP_OK) return false;
    esp_netif_dns_info_t dns = {0}, check = {0};
    dns.ip.type = ESP_IPADDR_TYPE_V4;
    dns.ip.u_addr.ip4 = ip.ip;
    uint8_t enabled = 1, offered = 0;
    if (esp_netif_set_dns_info(ap_netif, ESP_NETIF_DNS_MAIN, &dns) != ESP_OK ||
        esp_netif_dhcps_option(ap_netif, ESP_NETIF_OP_SET, ESP_NETIF_DOMAIN_NAME_SERVER,
                              &enabled, sizeof enabled) != ESP_OK ||
        // On a stopped interface this re-arms DHCP for the next AP start.
        // Leaving it explicitly STOPPED would suppress that automatic start.
        esp_netif_dhcps_start(ap_netif) != ESP_OK ||
        esp_netif_get_dns_info(ap_netif, ESP_NETIF_DNS_MAIN, &check) != ESP_OK ||
        esp_netif_dhcps_option(ap_netif, ESP_NETIF_OP_GET, ESP_NETIF_DOMAIN_NAME_SERVER,
                              &offered, sizeof offered) != ESP_OK ||
        !offered || check.ip.u_addr.ip4.addr != ip.ip.addr) return false;
    atomic_store(&dns_advertised, true);
    return true; // The default AP start handler starts DHCP after Wi-Fi starts.
}

static bool valid_credentials(const char *ssid, const char *password) {
    if (!ssid || !password || !*ssid || strlen(ssid) > 32 || strlen(password) > 64) return false;
    size_t n = strlen(password);
    if (n && n < 8) return false;
    if (n == 64 && strspn(password, "0123456789abcdefABCDEF") != 64) return false;
    for (const unsigned char *p = (const unsigned char *)ssid; *p; ++p) if (*p < 32 || *p == 127) return false;
    return true;
}

static bool store_credentials(const wifi_credentials_t *value) {
    nvs_handle_t handle;
    if (nvs_open("meeting_wifi", NVS_READWRITE, &handle) != ESP_OK) return false;
    esp_err_t error = nvs_set_blob(handle, "network", value, sizeof *value);
    if (error == ESP_OK) error = nvs_commit(handle);
    wifi_credentials_t check = {0};
    size_t length = sizeof check;
    if (error == ESP_OK) error = nvs_get_blob(handle, "network", &check, &length);
    bool ok = error == ESP_OK && length == sizeof check && memcmp(&check, value, length) == 0;
    memset(&check, 0, sizeof check);
    nvs_close(handle);
    return ok;
}

static void network_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg;
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        atomic_store(&linked, true);
        xEventGroupSetBits(events, LINK_UP);
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        atomic_store(&linked, false);
        if (!atomic_load(&ignore_disconnect)) {
            const wifi_event_sta_disconnected_t *event = data;
            atomic_store(&last_reason, event ? event->reason : 0);
            xEventGroupSetBits(events, LINK_FAILED);
        }
    }
}

static bool start_radio(wifi_mode_t mode) {
    if (esp_wifi_set_mode(mode) != ESP_OK) return false;
    if (!started) {
        if (esp_wifi_start() != ESP_OK) return false;
        started = true;
    }
    return true;
}

static bool connect_network(const wifi_credentials_t *value, bool keep_ap) {
    atomic_store(&connecting, true);
    atomic_store(&ignore_disconnect, true);
    esp_wifi_disconnect();
    atomic_store(&linked, false);
    wifi_config_t config = {0};
    memcpy(config.sta.ssid, value->ssid, strlen(value->ssid));
    memcpy(config.sta.password, value->password, strlen(value->password));
    config.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    config.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    config.sta.pmf_cfg.capable = true;
    bool ok = start_radio(keep_ap ? WIFI_MODE_APSTA : WIFI_MODE_STA) &&
              esp_wifi_set_config(WIFI_IF_STA, &config) == ESP_OK;
    memset(&config, 0, sizeof config);
    // Let any intentional-disconnect notification finish before waiting for the new association.
    vTaskDelay(pdMS_TO_TICKS(100));
    atomic_store(&ignore_disconnect, false);
    if (ok) {
        ok = false;
        for (unsigned attempt = 0; attempt < 3; ++attempt) {
            xEventGroupClearBits(events, LINK_UP | LINK_FAILED);
            atomic_store(&last_reason, 0);
            if (esp_wifi_connect() != ESP_OK) break;
            EventBits_t bits = xEventGroupWaitBits(events, LINK_UP | LINK_FAILED, pdFALSE, pdFALSE, pdMS_TO_TICKS(8000));
            if ((bits & LINK_UP) && atomic_load(&linked)) { ok = true; break; }
            atomic_store(&ignore_disconnect, true);
            esp_wifi_disconnect();
            vTaskDelay(pdMS_TO_TICKS(200));
            atomic_store(&ignore_disconnect, false);
            int reason = atomic_load(&last_reason);
            if (reason == WIFI_REASON_AUTH_FAIL || reason == WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT) break;
        }
    }
    if (ok) esp_wifi_set_ps(WIFI_PS_NONE);
    atomic_store(&connecting, false);
    return ok;
}

static bool socket_ipv4(const struct sockaddr_storage *address, socklen_t size, uint32_t *host_ip) {
    uint32_t network_ip;
    if (address->ss_family == AF_INET && size >= sizeof(struct sockaddr_in)) {
        network_ip = ((const struct sockaddr_in *)address)->sin_addr.s_addr;
    } else if (address->ss_family == AF_INET6 && size >= sizeof(struct sockaddr_in6)) {
        // IDF's dual-stack HTTP listener reports IPv4 clients as ::ffff:a.b.c.d.
        // A sockaddr_in truncates that address and incorrectly rejects every client.
        const uint8_t *bytes = (const uint8_t *)&((const struct sockaddr_in6 *)address)->sin6_addr;
        const uint8_t mapped_prefix[12] = {0,0,0,0,0,0,0,0,0,0,0xff,0xff};
        if (memcmp(bytes, mapped_prefix, sizeof mapped_prefix) != 0) return false;
        memcpy(&network_ip, bytes + sizeof mapped_prefix, sizeof network_ip);
    } else return false;
    *host_ip = ntohl(network_ip);
    return true;
}

static bool restore_confirmed_driver_config(void) {
    wifi_config_t config = {0};
    if (atomic_load(&has_saved)) {
        memcpy(config.sta.ssid, saved.ssid, strlen(saved.ssid));
        memcpy(config.sta.password, saved.password, strlen(saved.password));
    }
    // The official manager writes its candidate to the driver's namespace.
    // Restore from our confirmed record after cancellation, and after a reboot
    // during provisioning, before any connection can use that candidate.
    bool ok = esp_wifi_set_mode(WIFI_MODE_STA) == ESP_OK &&
              esp_wifi_set_storage(WIFI_STORAGE_FLASH) == ESP_OK &&
              esp_wifi_set_config(WIFI_IF_STA, &config) == ESP_OK;
    memset(&config, 0, sizeof config);
    return esp_wifi_set_storage(WIFI_STORAGE_RAM) == ESP_OK && ok;
}

// Restrict both the client subnet and destination interface to the device AP.
static bool ap_client(httpd_req_t *request) {
    struct sockaddr_storage peer = {0}, local = {0};
    socklen_t peer_size = sizeof peer, local_size = sizeof local;
    uint32_t peer_ip, local_ip;
    int fd = httpd_req_to_sockfd(request);
    return atomic_load(&portal) &&
           getpeername(fd, (struct sockaddr *)&peer, &peer_size) == 0 &&
           getsockname(fd, (struct sockaddr *)&local, &local_size) == 0 &&
           socket_ipv4(&peer, peer_size, &peer_ip) && socket_ipv4(&local, local_size, &local_ip) &&
           (peer_ip & 0xFFFFFF00u) == 0xC0A80400u && local_ip == 0xC0A80401u;
}

static esp_err_t json_reply(httpd_req_t *request, cJSON *value) {
    char *encoded = cJSON_PrintUnformatted(value);
    cJSON_Delete(value);
    if (!encoded) return httpd_resp_send_err(request, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
    httpd_resp_set_type(request, "application/json; charset=utf-8");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    esp_err_t result = httpd_resp_sendstr(request, encoded);
    cJSON_free(encoded);
    return result;
}

static esp_err_t page(httpd_req_t *request) {
    if (!ap_client(request)) return httpd_resp_send_err(request, HTTPD_403_FORBIDDEN, "Connect to device Wi-Fi");
    atomic_fetch_add(&page_views, 1);
    httpd_resp_set_type(request, "text/html; charset=utf-8");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'");
    return httpd_resp_sendstr(request, portal_html_start);
}

static esp_err_t get_status(httpd_req_t *request) {
    if (!ap_client(request)) return httpd_resp_send_err(request, HTTPD_403_FORBIDDEN, "Connect to device Wi-Fi");
    cJSON *out = cJSON_CreateObject();
    if (!out) return ESP_FAIL;
    cJSON_AddStringToObject(out, "device", ap_name);
    cJSON_AddStringToObject(out, "token", csrf);
    cJSON_AddStringToObject(out, "message", atomic_load(&setup_message));
    cJSON_AddBoolToObject(out, "busy", atomic_load(&busy));
    cJSON_AddBoolToObject(out, "connected", atomic_load(&linked));
    cJSON_AddBoolToObject(out, "saved", atomic_load(&has_saved));
    cJSON_AddBoolToObject(out, "complete", atomic_load(&setup_complete));
    return json_reply(request, out);
}

static esp_err_t scan(httpd_req_t *request) {
    if (!ap_client(request)) return httpd_resp_send_err(request, HTTPD_403_FORBIDDEN, "Connect to device Wi-Fi");
    if (atomic_load(&busy)) return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Connection in progress");
    wifi_scan_config_t config = {.show_hidden = false};
    if (esp_wifi_scan_start(&config, true) != ESP_OK) return httpd_resp_send_err(request, HTTPD_500_INTERNAL_SERVER_ERROR, "Scan unavailable");
    wifi_ap_record_t found[16];
    uint16_t count = 16;
    if (esp_wifi_scan_get_ap_records(&count, found) != ESP_OK) return ESP_FAIL;
    cJSON *out = cJSON_CreateArray();
    if (!out) return ESP_FAIL;
    for (unsigned i = 0; i < count; ++i) {
        if (!found[i].ssid[0]) continue;
        bool duplicate = false;
        for (unsigned j = 0; j < i; ++j) if (memcmp(found[i].ssid, found[j].ssid, 32) == 0) duplicate = true;
        if (duplicate) continue;
        char ssid[33] = {0};
        memcpy(ssid, found[i].ssid, 32);
        cJSON *item = cJSON_CreateObject();
        if (!item) break;
        cJSON_AddStringToObject(item, "ssid", ssid);
        cJSON_AddNumberToObject(item, "signal", found[i].rssi);
        cJSON_AddBoolToObject(item, "secured", found[i].authmode != WIFI_AUTH_OPEN);
        cJSON_AddItemToArray(out, item);
    }
    return json_reply(request, out);
}

static esp_err_t configure(httpd_req_t *request) {
    if (!ap_client(request)) return httpd_resp_send_err(request, HTTPD_403_FORBIDDEN, "Connect to device Wi-Fi");
    char token[40] = {0};
    if (httpd_req_get_hdr_value_str(request, "X-Setup-Token", token, sizeof token) != ESP_OK || strcmp(token, csrf) != 0)
        return httpd_resp_send_err(request, HTTPD_403_FORBIDDEN, "Reload setup page");
    if (request->content_len <= 0 || request->content_len > 1024 || atomic_load(&busy))
        return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Invalid or busy request");
    char body[1025] = {0};
    size_t used = 0;
    while (used < request->content_len) {
        int got = httpd_req_recv(request, body + used, request->content_len - used);
        if (got <= 0) { memset(body, 0, sizeof body); return ESP_FAIL; }
        used += got;
    }
    cJSON *input = cJSON_Parse(body);
    cJSON *ssid = cJSON_GetObjectItemCaseSensitive(input, "ssid");
    cJSON *password = cJSON_GetObjectItemCaseSensitive(input, "password");
    bool valid = !strstr(body, "\\u0000") && cJSON_IsString(ssid) && cJSON_IsString(password) &&
                 valid_credentials(ssid->valuestring, password->valuestring);
    memset(body, 0, sizeof body);
    if (valid && xSemaphoreTake(config_lock, pdMS_TO_TICKS(100)) == pdTRUE) {
        memset(&pending, 0, sizeof pending);
        pending.version = 1;
        snprintf(pending.ssid, sizeof pending.ssid, "%s", ssid->valuestring);
        snprintf(pending.password, sizeof pending.password, "%s", password->valuestring);
        atomic_store(&busy, true);
        atomic_store(&candidate_ready, true);
        atomic_store(&setup_state, MEETING_WIFI_VERIFY);
        atomic_store(&setup_message, "正在验证网络连接");
        xSemaphoreGive(config_lock);
    } else valid = false;
    if (cJSON_IsString(password)) memset(password->valuestring, 0, strlen(password->valuestring));
    cJSON_Delete(input);
    if (!valid) return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Check Wi-Fi name and password");
    return httpd_resp_sendstr(request, "{\"accepted\":true}");
}

static esp_err_t captive_redirect(httpd_req_t *request) {
    if (!ap_client(request)) return httpd_resp_send_err(request, HTTPD_403_FORBIDDEN, "Connect to device Wi-Fi");
    atomic_fetch_add(&redirects, 1);
    httpd_resp_set_status(request, "302 Found");
    httpd_resp_set_type(request, "text/html; charset=utf-8");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store, no-cache, must-revalidate");
    httpd_resp_set_hdr(request, "Location", portal_url);
    // iOS needs a nonempty response. Android/Windows must not receive their
    // expected 204/success text, which would suppress the system sign-in UI.
    return httpd_resp_sendstr(request, "<!doctype html><html><body><a href=\"http://192.168.4.1/\">Open Folo Wi-Fi setup</a></body></html>");
}

static esp_err_t captive_not_found(httpd_req_t *request, httpd_err_code_t error) {
    (void)error;
    return captive_redirect(request);
}

static void dns_task(void *arg) {
    (void)arg;
    int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    struct sockaddr_in address = {.sin_family = AF_INET, .sin_port = htons(53), .sin_addr.s_addr = htonl(INADDR_ANY)};
    struct timeval timeout = {.tv_usec = 200000};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout);
    bool bound = fd >= 0 && bind(fd, (struct sockaddr *)&address, sizeof address) == 0;
    if (bound) xEventGroupSetBits(events, DNS_READY);
    while (bound && atomic_load(&dns_running)) {
        uint8_t packet[512];
        struct sockaddr_in client;
        socklen_t client_size = sizeof client;
        int length = recvfrom(fd, packet, 480, 0, (struct sockaddr *)&client, &client_size);
        if (length < 17 || (ntohl(client.sin_addr.s_addr) & 0xFFFFFF00u) != 0xC0A80400u ||
            packet[4] != 0 || packet[5] != 1 || packet[2] & 0x80) continue;
        int pos = 12;
        while (pos < length && packet[pos] && packet[pos] < 64) pos += packet[pos] + 1;
        if (pos + 5 > length || packet[pos] != 0) continue;
        atomic_fetch_add(&dns_queries, 1);
        ++pos;
        bool ipv4 = packet[pos] == 0 && packet[pos + 1] == 1 && packet[pos + 2] == 0 && packet[pos + 3] == 1;
        int end = pos + 4;
        packet[2] = 0x81; packet[3] = 0x80;
        packet[6] = 0; packet[7] = ipv4 ? 1 : 0;
        memset(packet + 8, 0, 4);
        if (ipv4) {
            const uint8_t answer[] = {0xc0,0x0c,0,1,0,1,0,0,0,30,0,4,192,168,4,1};
            memcpy(packet + end, answer, sizeof answer);
            end += sizeof answer;
        }
        sendto(fd, packet, end, 0, (struct sockaddr *)&client, client_size);
    }
    if (fd >= 0) close(fd);
    xEventGroupClearBits(events, DNS_READY);
    xEventGroupSetBits(events, DNS_DONE);
    vTaskDelete(NULL);
}

static void close_portal(void) {
    if (server) { httpd_stop(server); server = NULL; }
    if (atomic_exchange(&dns_running, false)) xEventGroupWaitBits(events, DNS_DONE, pdFALSE, pdTRUE, pdMS_TO_TICKS(1500));
    atomic_store(&portal, false);
    atomic_store(&setup_complete, false);
    close_at = 0;
    esp_wifi_set_mode(WIFI_MODE_STA);
}

static bool open_portal(void) {
    if (atomic_load(&portal)) return true;
    atomic_store(&ignore_disconnect, true);
    esp_wifi_disconnect();
    atomic_store(&linked, false);
    if (started) { esp_wifi_stop(); started = false; }
    if (esp_wifi_set_mode(WIFI_MODE_APSTA) != ESP_OK) return false;
    wifi_config_t ap = {0};
    memcpy(ap.ap.ssid, ap_name, strlen(ap_name));
    ap.ap.ssid_len = strlen(ap_name);
    ap.ap.channel = 1;
    ap.ap.max_connection = 2;
    ap.ap.authmode = WIFI_AUTH_OPEN;
    if (esp_wifi_set_config(WIFI_IF_AP, &ap) != ESP_OK) return false;
    memset(&ap, 0, sizeof ap);
    if (!configure_portal_dhcp()) return false;
    atomic_store(&dns_queries, 0);
    atomic_store(&redirects, 0);
    atomic_store(&page_views, 0);
    atomic_store(&setup_state, MEETING_WIFI_SETUP);
    atomic_store(&setup_message, "选择一个 2.4 GHz Wi-Fi");
    atomic_store(&portal, true);
    httpd_config_t http = HTTPD_DEFAULT_CONFIG();
    http.stack_size = 6144;
    http.max_open_sockets = 3;
    http.lru_purge_enable = true;
    http.uri_match_fn = httpd_uri_match_wildcard;
    http.recv_wait_timeout = 5;
    http.send_wait_timeout = 5;
    if (httpd_start(&server, &http) != ESP_OK) { close_portal(); return false; }
    const httpd_uri_t routes[] = {
        {.uri = "/", .method = HTTP_GET, .handler = page},
        {.uri = "/api/status", .method = HTTP_GET, .handler = get_status},
        {.uri = "/api/networks", .method = HTTP_GET, .handler = scan},
        {.uri = "/api/config", .method = HTTP_POST, .handler = configure},
        // Includes iOS hotspot-detect.html, Android generate_204 and Windows
        // connecttest.txt, plus vendor-specific HTTP connectivity probes.
        {.uri = "/*", .method = HTTP_GET, .handler = captive_redirect},
        {.uri = "/*", .method = HTTP_HEAD, .handler = captive_redirect},
    };
    for (unsigned i = 0; i < sizeof routes / sizeof routes[0]; ++i) {
        if (httpd_register_uri_handler(server, &routes[i]) != ESP_OK) { close_portal(); return false; }
    }
    if (httpd_register_err_handler(server, HTTPD_404_NOT_FOUND, captive_not_found) != ESP_OK) { close_portal(); return false; }
    xEventGroupClearBits(events, DNS_DONE | DNS_READY);
    atomic_store(&dns_running, true);
    if (xTaskCreate(dns_task, "setup_dns", 3072, NULL, 3, NULL) != pdPASS) {
        atomic_store(&dns_running, false);
        close_portal();
        return false;
    }
    EventBits_t ready = xEventGroupWaitBits(events, DNS_READY | DNS_DONE, pdFALSE, pdFALSE, pdMS_TO_TICKS(1500));
    // Advertise the SSID only after HTTP and DNS can answer the first probes.
    if (!(ready & DNS_READY) || !start_radio(WIFI_MODE_APSTA)) { close_portal(); return false; }
    return true;
}

bool meeting_wifi_init(void) {
    events = xEventGroupCreate();
    config_lock = xSemaphoreCreateMutex();
    if (!events || !config_lock || !esp_netif_create_default_wifi_sta()) return false;
    ap_netif = esp_netif_create_default_wifi_ap();
    if (!ap_netif) return false;
    wifi_init_config_t config = WIFI_INIT_CONFIG_DEFAULT();
    if (esp_wifi_init(&config) != ESP_OK || esp_wifi_set_storage(WIFI_STORAGE_RAM) != ESP_OK ||
        esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, network_event, NULL) != ESP_OK ||
        esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, network_event, NULL) != ESP_OK) return false;
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
    snprintf(ap_name, sizeof ap_name, "Folo-Meeting-%02X%02X", mac[4], mac[5]);
    for (unsigned i = 0; i < 16; ++i) snprintf(csrf + i * 2, 3, "%02x", (unsigned)(esp_random() & 255));
    nvs_handle_t handle;
    if (nvs_open("meeting_wifi", NVS_READONLY, &handle) == ESP_OK) {
        size_t size = sizeof saved;
        esp_err_t error = nvs_get_blob(handle, "network", &saved, &size);
        nvs_close(handle);
        bool valid = error == ESP_OK && size == sizeof saved && saved.version == 1 &&
                     saved.ssid[32] == 0 && saved.password[64] == 0 && valid_credentials(saved.ssid, saved.password);
        atomic_store(&has_saved, valid);
        if (!valid) memset(&saved, 0, sizeof saved);
    }
    if (!restore_confirmed_driver_config()) return false;
    if (atomic_load(&has_saved) && connect_network(&saved, false)) return true;
#ifdef CONFIG_FOLO_MEETING_COMPANION
    return true; // Companion provisioning uses BLE; no phone network switch.
#else
    return open_portal();
#endif
}

bool meeting_wifi_connected(void) { return atomic_load(&linked); }
bool meeting_wifi_saved(void) { return atomic_load(&has_saved); }
meeting_wifi_diagnostics_t meeting_wifi_diagnostics(void) {
    wifi_sta_list_t clients = {0};
    if (atomic_load(&portal)) esp_wifi_ap_get_sta_list(&clients);
    return (meeting_wifi_diagnostics_t) {
        .dns_advertised = atomic_load(&dns_advertised),
        .dns_ready = events && (xEventGroupGetBits(events) & DNS_READY),
        .clients = clients.num, .dns_queries = atomic_load(&dns_queries),
        .redirects = atomic_load(&redirects), .page_views = atomic_load(&page_views)
    };
}
// Exercise the real dual-stack listener over the AP IPv4 address without changing
// the computer network. Only an explicit idle USB command runs these requests.
static int probe_http(const char *path, const char *host, bool post, size_t *bytes, bool *content_ok) {
    *bytes = 0;
    *content_ok = false;
    int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (fd < 0) return 0;
    struct timeval timeout = {.tv_sec = 2};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout);
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof timeout);
    struct sockaddr_in local = {.sin_family = AF_INET, .sin_addr.s_addr = htonl(0xC0A80401u)};
    struct sockaddr_in target = local;
    target.sin_port = htons(80);
    int status = 0;
    if (bind(fd, (struct sockaddr *)&local, sizeof local) == 0 &&
        connect(fd, (struct sockaddr *)&target, sizeof target) == 0) {
        char request[320];
        int length = snprintf(request, sizeof request,
            "%s %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n"
            "X-Setup-Token: invalid-self-test\r\nContent-Length: 0\r\n\r\n", post ? "POST" : "GET", path, host);
        if (length > 0 && length < sizeof request && send(fd, request, length, 0) == length) {
            char prefix[1024] = {0}, chunk[512];
            size_t kept = 0;
            bool complete = false;
            int64_t deadline = esp_timer_get_time() + 4000000;
            while (*bytes < 32768 && esp_timer_get_time() < deadline) {
                int received = recv(fd, chunk, sizeof chunk, 0);
                if (received == 0) { complete = true; break; }
                if (received < 0) break;
                *bytes += received;
                size_t copy = sizeof prefix - 1 - kept;
                if (copy > received) copy = received;
                memcpy(prefix + kept, chunk, copy);
                kept += copy;
                // HTTP/1.1 may keep the socket alive after the whole response.
                // Completion is Content-Length bytes, not a TCP disconnect.
                const char *header_end = strstr(prefix, "\r\n\r\n");
                const char *length_field = strstr(prefix, "\r\nContent-Length: ");
                unsigned body_length;
                if (header_end && length_field && length_field < header_end &&
                    sscanf(length_field, "\r\nContent-Length: %u", &body_length) == 1 &&
                    body_length <= 32768 && *bytes >= (size_t)(header_end + 4 - prefix) + body_length) {
                    complete = true;
                    break;
                }
            }
            sscanf(prefix, "HTTP/%*d.%*d %d", &status);
            if (complete) {
                if (!strcmp(path, "/")) *content_ok = strstr(prefix, "连接AI通行证") != NULL;
                else if (status == 302) *content_ok = strstr(prefix, "Location: http://192.168.4.1/") != NULL;
                else *content_ok = true;
            }
            memset(prefix, 0, sizeof prefix);
            memset(chunk, 0, sizeof chunk);
        }
    }
    close(fd);
    return status;
}

void meeting_wifi_probe(void) {
    if (!atomic_load(&portal) || atomic_load(&busy)) {
        puts("{\"event\":\"portal_check_done\",\"ok\":false,\"available\":false}");
        fflush(stdout);
        return;
    }
    const struct { const char *path, *host; int expected; bool post; } checks[] = {
        {"/", "192.168.4.1", 200, false},
        {"/api/status", "192.168.4.1", 200, false},
        {"/hotspot-detect.html", "captive.apple.com", 302, false},
        {"/generate_204", "connectivitycheck.gstatic.com", 302, false},
        {"/connecttest.txt", "www.msftconnecttest.com", 302, false},
        {"/api/config", "192.168.4.1", 403, true},
    };
    bool all_ok = true;
    for (unsigned i = 0; i < sizeof checks / sizeof checks[0]; ++i) {
        size_t bytes;
        bool content_ok;
        int status = probe_http(checks[i].path, checks[i].host, checks[i].post, &bytes, &content_ok);
        bool ok = status == checks[i].expected && content_ok;
        all_ok &= ok;
        printf("{\"event\":\"portal_check\",\"path\":\"%s\",\"status\":%d,\"bytes\":%u,\"ok\":%s}\n",
               checks[i].path, status, (unsigned)bytes, ok ? "true" : "false");
        fflush(stdout);
    }
    printf("{\"event\":\"portal_check_done\",\"ok\":%s,\"available\":true}\n", all_ok ? "true" : "false");
    fflush(stdout);
}
void meeting_wifi_request_setup(void) { atomic_store(&setup_requested, true); }

bool meeting_wifi_connect(const char *ssid, const char *password, bool use_saved) {
    if (atomic_load(&portal)) close_portal();
    if (use_saved) return atomic_load(&has_saved) && (atomic_load(&linked) || connect_network(&saved, false));
    if (!valid_credentials(ssid, password)) return false;
    wifi_credentials_t value = {.version = 1};
    snprintf(value.ssid, sizeof value.ssid, "%s", ssid);
    snprintf(value.password, sizeof value.password, "%s", password);
    bool ok = connect_network(&value, false);
    memset(&value, 0, sizeof value);
    return ok;
}

bool meeting_wifi_provision(const char *ssid, const char *password) {
    if (!valid_credentials(ssid, password)) return false;
    wifi_credentials_t value = {.version = 1};
    snprintf(value.ssid, sizeof value.ssid, "%s", ssid);
    snprintf(value.password, sizeof value.password, "%s", password);
    bool ok = meeting_wifi_connect(ssid, password, false) && store_credentials(&value);
    if (ok) { memcpy(&saved, &value, sizeof saved); atomic_store(&has_saved, true); }
    else if (atomic_load(&has_saved)) connect_network(&saved, false);
    memset(&value, 0, sizeof value);
    return ok;
}

bool meeting_wifi_external_begin(void) {
    if (atomic_load(&portal)) close_portal();
    return start_radio(WIFI_MODE_STA);
}

bool meeting_wifi_external_commit(void) {
    if (!atomic_load(&linked)) return false;
    wifi_config_t config = {0};
    wifi_credentials_t value = {.version = 1};
    bool ok = esp_wifi_get_config(WIFI_IF_STA, &config) == ESP_OK;
    memcpy(value.ssid, config.sta.ssid, 32);
    memcpy(value.password, config.sta.password, 64);
    ok = ok && valid_credentials(value.ssid, value.password) && store_credentials(&value);
    if (ok) { memcpy(&saved, &value, sizeof saved); atomic_store(&has_saved, true); }
    memset(&value, 0, sizeof value);
    memset(&config, 0, sizeof config);
    return ok;
}

bool meeting_wifi_external_end(bool committed) {
    if (committed) {
        // Match connect_network(): a later recording reuses this live link.
        // It must not inherit the driver's default modem power saving.
        bool ok = esp_wifi_set_storage(WIFI_STORAGE_RAM) == ESP_OK;
        return esp_wifi_set_ps(WIFI_PS_NONE) == ESP_OK && ok;
    }
    esp_wifi_disconnect();
    atomic_store(&linked, false);
    bool ok = restore_confirmed_driver_config();
    return ok && (!atomic_load(&has_saved) || connect_network(&saved, false));
}

bool meeting_wifi_poll(void) {
    if (atomic_exchange(&setup_requested, false)) open_portal();
    if (atomic_exchange(&candidate_ready, false)) {
        wifi_credentials_t value = {0};
        xSemaphoreTake(config_lock, portMAX_DELAY);
        memcpy(&value, &pending, sizeof value);
        memset(&pending, 0, sizeof pending);
        xSemaphoreGive(config_lock);
        bool ok = connect_network(&value, true);
        if (ok && store_credentials(&value)) {
            memcpy(&saved, &value, sizeof saved);
            atomic_store(&has_saved, true);
            atomic_store(&setup_message, "连接成功, Wi-Fi 已保存");
            atomic_store(&setup_state, MEETING_WIFI_SETUP_DONE);
            atomic_store(&setup_complete, true);
            close_at = esp_timer_get_time() + 5000000;
        } else {
            atomic_store(&setup_state, MEETING_WIFI_SETUP_ERROR);
            int reason = atomic_load(&last_reason);
            atomic_store(&setup_message, ok ? "保存失败, 请重试" : reason == WIFI_REASON_NO_AP_FOUND
                ? "找不到网络, 请检查 2.4 GHz 热点" : "连接失败, 请检查密码和信号");
        }
        memset(&value, 0, sizeof value);
        atomic_store(&busy, false);
    }
    if (close_at && esp_timer_get_time() >= close_at) { close_portal(); return true; }
    return false;
}

void meeting_wifi_snapshot(meeting_status_t *view) {
    view->wifi_connected = atomic_load(&linked);
    if (atomic_load(&portal)) {
        view->state = atomic_load(&setup_state);
        snprintf(view->setup_ssid, sizeof view->setup_ssid, "%s", ap_name);
        snprintf(view->wifi_message, sizeof view->wifi_message, "%s", atomic_load(&setup_message));
    } else if (atomic_load(&connecting)) view->state = MEETING_WIFI;
}
