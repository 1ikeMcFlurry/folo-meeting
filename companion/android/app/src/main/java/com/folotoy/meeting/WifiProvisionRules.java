package com.folotoy.meeting;

import java.nio.charset.StandardCharsets;

final class WifiProvisionRules {
    static String validate(String ssid,String password,boolean open) {
        int length=ssid.getBytes(StandardCharsets.UTF_8).length;
        if(length==0 || length>32 || ssid.chars().anyMatch(c->c<32 || c==127))
            return "Wi-Fi 名称须为 1 至 32 个字节，请检查名称是否正确";
        int bytes=password.getBytes(StandardCharsets.UTF_8).length;
        if(open) return password.isEmpty()?null:"开放网络不应填写密码";
        if(bytes>=8 && bytes<=63) return null;
        if(password.matches("[a-fA-F0-9]{64}")) return null;
        return "Wi-Fi 密码须为 8 至 63 个字节，或 64 位十六进制密钥";
    }
}
