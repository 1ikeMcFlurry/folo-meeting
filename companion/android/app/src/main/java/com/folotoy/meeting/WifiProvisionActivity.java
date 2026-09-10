package com.folotoy.meeting;

import android.app.Activity;
import android.app.AlertDialog;
import android.bluetooth.BluetoothManager;
import android.bluetooth.le.*;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.*;
import android.text.InputType;
import android.view.*;
import android.view.inputmethod.InputMethodManager;
import android.widget.*;
import com.espressif.provisioning.*;
import com.espressif.provisioning.listeners.*;
import org.greenrobot.eventbus.*;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Official Espressif BLE protocol; the control link supplies an ephemeral PoP. */
@android.annotation.SuppressLint("MissingPermission")
public class WifiProvisionActivity extends BrandActivity {
    private static final int BRAND=BrandUi.PRESSED, INK=BrandUi.INK, MUTED=BrandUi.MUTED, BACK=BrandUi.BACK;
    private final Handler main=new Handler(Looper.getMainLooper());
    private ESPDevice device;
    private LinearLayout root,body;
    private TextView steps,status,backLink;
    private Button close;
    private enum Page { CONNECTING, NETWORKS, PASSWORD, FAILURE, SUCCESS, ERROR }
    private Page visiblePage=Page.CONNECTING;
    private AlertDialog exitDialog;
    private EditText password;
    private String selected="",deviceName="";
    private boolean secure,working,ending,committed,needsReset;
    private int epoch,currentStep=1,selectedSecurity=3;
    private BluetoothLeScanner scanner;
    private ScanCallback scanCallback;
    private Runnable timeout;
    private ArrayList<WiFiAccessPoint> networks=new ArrayList<>();

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        build();
        String pop=getIntent().getStringExtra("pop");
        getIntent().removeExtra("pop");
        if(saved!=null || pop==null || !pop.matches("[a-f0-9]{32}")) {
            fatal("配网会话已过期，请返回设备页重新开始。"); return;
        }
        deviceName=getIntent().getStringExtra("name");
        device=new ESPDevice(getApplicationContext(),ESPConstants.TransportType.TRANSPORT_BLE,ESPConstants.SecurityType.SECURITY_1);
        device.setProofOfPossession(pop);
        EventBus.getDefault().register(this);
        progress(1,"正在连接AI通行证","请让手机靠近AI通行证，保持蓝牙开启。");
        final int token=arm(35000,"连接设备超时。请靠近AI通行证，并重新开始配网。");
        main.postDelayed(()-> {
            if(!current(token)) return;
            try {
                // Discover the advertisement first: Android must learn that the
                // temporary address is random before it can connect correctly.
                String address=getIntent().getStringExtra("address");
                scanner=getSystemService(BluetoothManager.class).getAdapter().getBluetoothLeScanner();
                scanCallback=new ScanCallback() {
                    @Override public void onScanResult(int type,ScanResult result) {
                        main.post(()-> {
                            if(!current(token) || scanCallback==null || !address.equalsIgnoreCase(result.getDevice().getAddress())) return;
                            stopScan();
                            device.connectBLEDevice(result.getDevice(),getIntent().getStringExtra("uuid"));
                        });
                    }
                    @Override public void onScanFailed(int code) { ui(token,()->fatal("无法搜索配网设备，请检查蓝牙开关后重试。")); }
                };
                scanner.startScan(Collections.singletonList(new ScanFilter.Builder().setDeviceAddress(address).build()),
                    new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(),scanCallback);
            } catch(Exception ignored) { fatal("无法连接蓝牙。请检查附近设备权限和蓝牙开关。"); }
        },3500);
        main.postDelayed(()-> { if(!ending && !committed) fatal("本次配网已超过 5 分钟，请返回后重新开始。"); },295000);
    }
    private int dp(int x) { return Math.round(getResources().getDisplayMetrics().density*x); }
    private TextView label(String text,int size,int color,boolean bold) {
        TextView t=new TextView(this); t.setText(text); t.setTextSize(size); t.setTextColor(color);
        t.setLineSpacing(dp(4),1.05f); if(bold)t.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        return t;
    }
    private void build() {
        root=new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setBackgroundColor(BACK);
        root.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
        root.setPadding(dp(22),dp(20),dp(22),dp(16));
        root.setOnApplyWindowInsetsListener((v,i)-> {
            if(Build.VERSION.SDK_INT>=30) {
                android.graphics.Insets b=i.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.ime());
                root.setPadding(dp(22)+b.left,dp(20)+b.top,dp(22)+b.right,dp(16)+b.bottom);
            } else root.setPadding(dp(22),dp(20)+i.getSystemWindowInsetTop(),dp(22),dp(16)+i.getSystemWindowInsetBottom());
            return i;
        });
        setContentView(root);
        backLink=BrandUi.back(this,"返回设备",this::navigateBack); root.addView(backLink);
        TextView title=label("连接 Wi-Fi",29,INK,true); title.setPadding(0,dp(14),0,dp(16)); root.addView(title);
        steps=label("",13,BRAND,true); root.addView(steps);
        status=label("",14,MUTED,false); status.setPadding(0,dp(12),0,dp(12)); root.addView(status);
        status.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        ScrollView scroll=new ScrollView(this); scroll.setFillViewport(true);
        body=new LinearLayout(this); body.setOrientation(LinearLayout.VERTICAL); scroll.addView(body);
        root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        close=button("退出配网",this::requestLeave,false); root.addView(close);
    }
    private Button button(String title,Runnable action,boolean primary) {
        Button b=new Button(this); b.setText(title); b.setGravity(Gravity.CENTER); BrandUi.button(this,b,primary);
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2); p.setMargins(0,dp(6),0,dp(8)); b.setLayoutParams(p);
        b.setOnClickListener(v->action.run()); return b;
    }
    private void note(String text) { TextView t=label(text,14,MUTED,false); t.setPadding(0,dp(8),0,dp(14)); body.addView(t); }
    private void page(int step,String text) {
        currentStep=step;
        if(password!=null) { password.getText().clear(); password=null; }
        body.removeAllViews(); status.setText(text);
        steps.setText((step>1?"✓":"1")+" 选择网络    ·    "+(step>2?"✓":"2")+" 输入密码    ·    "+(step>3?"✓":"3")+" 完成连接");
    }
    private void progress(int step,String title,String detail) {
        setPage(Page.CONNECTING); page(step,title); working=true;
        ProgressBar bar=new ProgressBar(this); LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(dp(40),dp(40));
        p.setMargins(0,dp(30),0,dp(22)); body.addView(bar,p); note(detail);
    }
    private int arm(long ms,String message) {
        disarm(); int value=epoch;
        timeout=()-> { if(current(value)) fatal(message); };
        main.postDelayed(timeout,ms); return value;
    }
    private void disarm() { epoch++; if(timeout!=null)main.removeCallbacks(timeout); timeout=null; }
    private boolean current(int token) { return !ending && !isDestroyed() && token==epoch; }
    private void ui(int token,Runnable action) { main.post(()-> { if(current(token)) action.run(); }); }

    @Subscribe(threadMode=ThreadMode.MAIN) public void onConnection(DeviceConnectionEvent event) {
        if(ending || committed) return;
        if(event.getEventType()==ESPConstants.EVENT_DEVICE_CONNECTED) {
            try {
                JSONObject info=new JSONObject(device.getVersionInfo()).getJSONObject("prov");
                if(info.optInt("sec_ver",-1)!=1 || device.getDeviceCapabilities().contains("no_pop"))
                    throw new IllegalStateException();
                progress(1,"正在验证AI通行证","正在建立安全连接，请稍候。");
                int token=arm(20000,"设备安全验证超时，请重新开始配网。");
                device.initSession(new ResponseListener() {
                    public void onSuccess(byte[] data) { ui(token,()-> { secure=true; scan(); }); }
                    public void onFailure(Exception error) { ui(token,()->fatal("设备安全验证失败，请返回设备页重新连接。")); }
                });
            } catch(Exception ignored) { fatal("设备配网版本或安全验证不匹配，请更新固件后重试。"); }
        } else if(event.getEventType()==ESPConstants.EVENT_DEVICE_CONNECTION_FAILED || event.getEventType()==ESPConstants.EVENT_DEVICE_DISCONNECTED) {
            fatal("蓝牙连接已断开，设备会恢复上次保存的网络。请靠近AI通行证后重新开始。");
        }
    }
    private void scan() {
        if(ending || !secure) return;
        progress(1,"正在搜索附近 Wi-Fi","AI通行证仅支持 2.4 GHz 网络，请靠近路由器。");
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_SECURE);
        int token=arm(35000,"搜索网络超时，请重新开始配网。");
        device.scanNetworks(new WiFiScanListener() {
            public void onWifiListReceived(ArrayList<WiFiAccessPoint> found) { ui(token,()-> {
                disarm(); working=false; networks=found; showNetworks();
            }); }
            public void onWiFiScanFailed(Exception error) { ui(token,()->fatal("搜索网络未完成，请返回后重试。")); }
        });
    }
    private void showNetworks() {
        hideKeyboard(); setPage(Page.NETWORKS); page(1,"选择AI通行证要连接的 Wi-Fi"); working=false;
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_SECURE);
        note(deviceName+" · 仅支持 2.4 GHz 网络");
        List<WiFiAccessPoint> ordered=new ArrayList<>(networks);
        ordered.sort(Comparator.comparingInt(WiFiAccessPoint::getRssi).reversed());
        Set<String> seen=new HashSet<>();
        for(WiFiAccessPoint ap:ordered) {
            String name=ap.getWifiName(); if(name==null || name.isEmpty() || !seen.add(name)) continue;
            String strength=ap.getRssi()>=-60?"信号强":ap.getRssi()>=-75?"信号良好":"信号较弱";
            String lock=ap.getSecurity()==0?"开放网络":"需要密码";
            Button item=button(name+"\n"+strength+" · "+lock,()->form(name,ap.getSecurity(),false),false);
            item.setGravity(Gravity.START|Gravity.CENTER_VERTICAL); body.addView(item);
        }
        if(seen.isEmpty()) note("暂未找到网络。请靠近路由器，或手动添加隐藏网络。");
        body.addView(button("重新搜索",this::scan,false));
        body.addView(button("手动添加隐藏网络",()->form("",3,true),false));
    }
    private void form(String name,int auth,boolean manual) {
        if(working) return;
        setPage(Page.PASSWORD); page(2,manual?"添加隐藏网络":name); selected=name; selectedSecurity=auth;
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        if(auth==1 || auth==5 || auth>7) {
            note("当前配网仅支持个人 Wi-Fi。请选择 WPA / WPA2 / WPA3 个人网络，或开放网络。");
            body.addView(button("选择其他网络",this::showNetworks,false)); return;
        }
        EditText ssid=new EditText(this); ssid.setSingleLine(true); ssid.setTextSize(16);
        ssid.setHint("Wi-Fi 名称"); ssid.setContentDescription("Wi-Fi 名称");
        if(manual) body.addView(ssid,new LinearLayout.LayoutParams(-1,dp(56)));
        CheckBox open=new CheckBox(this); open.setText("这是无需密码的开放网络"); open.setMinHeight(dp(48));
        open.setChecked(auth==0); if(manual) body.addView(open);
        password=new EditText(this); password.setSingleLine(true); password.setTextSize(16);
        password.setHint("输入 Wi-Fi 密码"); password.setContentDescription("Wi-Fi 密码");
        password.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);
        password.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO); password.setSaveEnabled(false);
        body.addView(password,new LinearLayout.LayoutParams(-1,dp(56)));
        password.setVisibility(open.isChecked()?View.GONE:View.VISIBLE);
        CheckBox show=new CheckBox(this); show.setText("显示密码"); show.setMinHeight(dp(48));
        show.setVisibility(open.isChecked()?View.GONE:View.VISIBLE); body.addView(show);
        show.setOnCheckedChangeListener((b,checked)-> { if(password!=null) {
            password.setInputType(InputType.TYPE_CLASS_TEXT|(checked?InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD:InputType.TYPE_TEXT_VARIATION_PASSWORD));
            password.setSelection(password.length());
        }});
        open.setOnCheckedChangeListener((b,checked)-> { if(password!=null) {
            password.getText().clear(); password.setVisibility(checked?View.GONE:View.VISIBLE); show.setVisibility(checked?View.GONE:View.VISIBLE);
        }});
        note(auth==0?"开放网络没有 Wi-Fi 密码，请确认这是你要连接的网络。":"密码通过加密蓝牙发送给AI通行证。只有联网验证成功后，设备才会确认保存。");
        body.addView(button("连接这个网络",()-> {
            String network=manual?ssid.getText().toString():name;
            String pass=open.isChecked()?"":password.getText().toString();
            String error=WifiProvisionRules.validate(network,pass,open.isChecked());
            if(error!=null) { status.setText(error); return; }
            selected=network; selectedSecurity=open.isChecked()?0:auth;
            ((InputMethodManager)getSystemService(INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(root.getWindowToken(),0);
            provision(network,pass);
        },true));
        body.addView(button("选择其他网络",this::showNetworks,false));
    }
    private void provision(String ssid,String pass) {
        progress(3,"正在发送 Wi-Fi 设置","正在将网络名称和密码安全发送给AI通行证。");
        int token=arm(75000,"连接 Wi-Fi 超时。请检查密码和路由器网络设置，再重试。");
        Runnable send=()->device.provision(ssid,pass,new ProvisionListener() {
            public void createSessionFailed(Exception e) { ui(token,()->fatal("安全会话已失效，请重新开始配网。")); }
            public void wifiConfigSent() { ui(token,()->status.setText("网络配置已送达AI通行证")); }
            public void wifiConfigFailed(Exception e) { ui(token,()->fatal("发送网络配置失败，请重新开始配网。")); }
            public void wifiConfigApplied() { ui(token,()->progress(3,"正在连接 Wi-Fi","AI通行证正在连接路由器，请稍候。")); }
            public void wifiConfigApplyFailed(Exception e) { ui(token,()->fatal("设备未能应用网络配置，请重新开始配网。")); }
            public void provisioningFailedFromDevice(ESPConstants.ProvisionFailureReason reason) { ui(token,()-> {
                disarm(); working=false; needsReset=true;
                setPage(Page.FAILURE); page(3,"连接未完成");
                note(reason==ESPConstants.ProvisionFailureReason.AUTH_FAILED?"Wi-Fi 密码错误，请重新输入。":
                     reason==ESPConstants.ProvisionFailureReason.NETWORK_NOT_FOUND?"AI通行证找不到这个网络。请检查 2.4 GHz Wi-Fi 是否开启，并靠近路由器。":"设备连接失败，请检查信号和路由器设置。");
                note("上次确认过的网络配置仍然保留。");
                String retry=reason==ESPConstants.ProvisionFailureReason.AUTH_FAILED?"修改密码后重试":"检查网络后重试";
                body.addView(button(retry,()->form(selected,selectedSecurity,false),true));
                body.addView(button("选择其他网络",WifiProvisionActivity.this::showNetworks,false));
            }); }
            public void deviceProvisioningSuccess() { ui(token,WifiProvisionActivity.this::commit); }
            public void onProvisioningFailed(Exception e) { ui(token,()->fatal("网络验证未完成，请返回后重试。")); }
        });
        if(needsReset) {
            device.resetWifiStatus(new ResponseListener() {
                public void onSuccess(byte[] bytes) { ui(token,()-> { needsReset=false; send.run(); }); }
                public void onFailure(Exception e) { ui(token,()->fatal("设备重试状态未恢复，请重新开始配网。")); }
            });
        } else send.run();
    }
    private void commit() {
        progress(3,"Wi-Fi 已连接，正在保存","正在确认AI通行证已保存设置，请稍候。");
        int token=arm(12000,"保存结果未能确认。请返回设备页重新连接，检查设备 Wi-Fi 状态。");
        device.sendDataToCustomEndPoint("folo-finish","commit".getBytes(StandardCharsets.UTF_8),new ResponseListener() {
            public void onSuccess(byte[] bytes) { ui(token,()-> {
                try { if(!new JSONObject(new String(bytes,StandardCharsets.UTF_8)).optBoolean("ok")) throw new IllegalStateException(); }
                catch(Exception e) { fatal("设备未确认保存，请返回设备页检查网络状态。"); return; }
                disarm(); committed=true; working=false;
                if(exitDialog!=null) exitDialog.dismiss();
                getWindow().clearFlags(WindowManager.LayoutParams.FLAG_SECURE);
                setPage(Page.SUCCESS); page(4,"Wi-Fi 已连接并保存");
                body.addView(label("✓  连接完成",25,0xff147d60,true));
                note(selected); note("设置已保存，下次开机会自动连接。返回后，App 会重新连接AI通行证。");
                body.addView(button("完成，返回设备",WifiProvisionActivity.this::leave,true));
                close.setVisibility(View.GONE);
                device.disconnectDevice();
            }); }
            public void onFailure(Exception e) { ui(token,()->fatal("保存结果未能确认，请返回设备页检查设备状态。")); }
        });
    }
    private void fatal(String message) {
        if(ending || committed) return;
        disarm(); stopScan(); working=false; secure=false;
        if(exitDialog!=null) exitDialog.dismiss();
        // Disconnect triggers the device's bounded cancellation and restoration.
        if(device!=null) { ESPDevice old=device; device=null; old.disconnectDevice(); }
        setPage(Page.ERROR); page(currentStep,"配网未完成"); note(message);
        note("AI通行证会恢复上次确认过的网络。也可以长按设备上键退出配网。");
        body.addView(button("返回设备，重新开始",this::leave,true));
    }
    private void leave() {
        if(ending) return; ending=true; disarm(); stopScan();
        if(password!=null) password.getText().clear();
        if(device!=null) { device.setProofOfPossession(""); device.disconnectDevice(); }
        setResult(committed?RESULT_OK:RESULT_CANCELED); finish();
    }
    private void setPage(Page page) {
        visiblePage=page;
        String parent=page==Page.PASSWORD || page==Page.FAILURE?"返回网络列表":"返回设备";
        backLink.setText("‹  "+parent); backLink.setContentDescription(parent);
    }
    @Override protected void navigateBack() {
        hideKeyboard();
        if(!working && secure && (visiblePage==Page.PASSWORD || visiblePage==Page.FAILURE)) showNetworks();
        else requestLeave();
    }
    private void requestLeave() {
        hideKeyboard();
        if(!working || committed) { leave(); return; }
        if(exitDialog!=null && exitDialog.isShowing()) return;
        exitDialog=new AlertDialog.Builder(this).setTitle("退出本次配网？")
            .setMessage("配网尚未完成。退出后，AI通行证会恢复上次保存的网络。")
            .setNegativeButton("继续配网",null).setPositiveButton("退出配网",(d,w)->leave()).create();
        exitDialog.show();
    }
    private void stopScan() {
        if(scanner!=null && scanCallback!=null) {
            try { scanner.stopScan(scanCallback); } catch(SecurityException ignored) {}
        }
        scanCallback=null; scanner=null;
    }
    @Override protected void onDestroy() {
        ending=true; stopScan(); main.removeCallbacksAndMessages(null);
        if(EventBus.getDefault().isRegistered(this)) EventBus.getDefault().unregister(this);
        if(device!=null) { device.setProofOfPossession(""); device.disconnectDevice(); }
        super.onDestroy();
    }
}
