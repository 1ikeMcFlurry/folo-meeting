package com.folotoy.meeting;

import android.Manifest;
import android.app.*;
import android.bluetooth.BluetoothDevice;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.*;
import android.text.*;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;

public class MainActivity extends BrandActivity {
    private static final int INK=BrandUi.INK, MUTED=BrandUi.MUTED, BRAND=BrandUi.PRESSED, BACK=BrandUi.BACK, LINE=BrandUi.LINE;
    private final ExecutorService work=Executors.newSingleThreadExecutor();
    private final Handler main=new Handler(Looper.getMainLooper());
    private Vault vault;
    private BleClient ble;
    private Feishu feishu;
    private LinearLayout root,content,nav;
    private TextView notice;
    private JSONObject settings=new JSONObject(),index=new JSONObject(),device=new JSONObject();
    private MeetingDraft draft;
    private String tab="设备",editingId="",settingsSection="";
    private boolean loading, editing, authRunning;
    private final Map<String,EditText> fields=new LinkedHashMap<>();
    private final Map<String,String> savedFields=new LinkedHashMap<>();
    private int savedExportMode;
    private boolean savedAudioOutput;
    private CheckBox audioOutput;
    private EditText titleEditor,bodyEditor,transcriptEditor;
    private Spinner exportMode;
    private final LinkedHashMap<String,BluetoothDevice> found=new LinkedHashMap<>();
    private LinearLayout deviceList;
    private Runnable draftSave;
    private BluetoothDevice provisionReturnDevice;
    private ScrollView pageScroll;
    private final Map<String,Integer> scrollPositions=new HashMap<>();

    @Override public void onCreate(Bundle state) {
        super.onCreate(state); vault=new Vault(this); ble=new BleClient(this); feishu=new Feishu(vault);
        ble.onDisconnected(()-> {
            if(!isDestroyed() && !loading && !editing && tab.equals("设备")) {
                layout(); notice.setText("蓝牙已断开。录音由设备独立完成，结束后可重新连接同步。");
            }
        });
        try {
            // Only a debug source set supplies this importer; release builds have no bootstrap path.
            Class<?> bootstrap=Class.forName("com.folotoy.meeting.DebugBootstrap");
            bootstrap.getMethod("run",android.content.Context.class).invoke(null,this);
        } catch(ClassNotFoundException ignored) {} catch(Exception ignored) {}
        try { settings=vault.load("settings"); index=vault.load("index"); }
        catch(Exception e) { new AlertDialog.Builder(this).setMessage("本地资料无法解密，原文件已保留。请检查应用数据或联系开发者。").setPositiveButton("知道了",null).show(); }
        layout();
        resumeAuthorization();
    }
    private int dp(int value) { return Math.round(getResources().getDisplayMetrics().density*value); }
    private TextView text(String value,int size,int color,boolean bold) {
        TextView view=new TextView(this); view.setText(value); view.setTextSize(size); view.setTextColor(color);
        view.setLineSpacing(dp(3),1.1f); if(bold) view.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        return view;
    }
    private void space(int height) { View view=new View(this); content.addView(view,new LinearLayout.LayoutParams(1,dp(height))); }
    private void line() { View view=new View(this); view.setBackgroundColor(LINE); LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,dp(1)); p.setMargins(0,dp(18),0,dp(18)); content.addView(view,p); }
    private void note(String value) { content.addView(text(value,14,MUTED,false)); space(12); }
    private void heading(String value) { content.addView(text(value,21,INK,true)); space(12); }
    private Button button(String value,Runnable action,boolean primary) {
        Button b=new Button(this); b.setText(value); BrandUi.button(this,b,primary);
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2); p.setMargins(0,dp(5),0,dp(7)); content.addView(b,p);
        b.setOnClickListener(v-> { if(!loading) action.run(); }); return b;
    }
    private EditText field(String label,String key,boolean secret,String fallback) {
        content.addView(text(label,14,MUTED,true));
        EditText edit=new EditText(this); edit.setSingleLine(true); edit.setTextSize(16); edit.setTextColor(INK);
        edit.setPadding(dp(10),dp(8),dp(10),dp(8)); edit.setSelectAllOnFocus(false);
        edit.setInputType(secret?android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD:
            android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        edit.setText(settings.optString(key,fallback)); edit.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        edit.setContentDescription(label);
        edit.setSaveEnabled(false);
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,dp(52)); p.setMargins(0,dp(4),0,dp(15)); content.addView(edit,p);
        fields.put(key,edit); return edit;
    }
    private void layout() {
        root=new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setBackgroundColor(BACK);
        root.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
        root.setPadding(dp(20),dp(18),dp(20),dp(10));
        root.setOnApplyWindowInsetsListener((v,insets)-> {
            if(Build.VERSION.SDK_INT>=30) {
                android.graphics.Insets bars=insets.getInsets(WindowInsets.Type.systemBars());
                root.setPadding(dp(20)+bars.left,dp(18)+bars.top,dp(20)+bars.right,dp(10)+bars.bottom);
            } else root.setPadding(dp(20)+insets.getSystemWindowInsetLeft(),dp(18)+insets.getSystemWindowInsetTop(),
                dp(20)+insets.getSystemWindowInsetRight(),dp(10)+insets.getSystemWindowInsetBottom());
            return insets;
        });
        setContentView(root);
        boolean child=editing || !settingsSection.isEmpty();
        if(child) root.addView(BrandUi.back(this,editing?"返回会议记录":"返回设置",this::navigateBack));
        else root.addView(text("FoloToy 会议",15,BRAND,true));
        TextView title=text(editing?"编辑纪要":!settingsSection.isEmpty()?settingsSection:tab,30,INK,true); title.setPadding(0,dp(10),0,dp(10)); root.addView(title);
        notice=text(loading?"正在处理…":"",13,MUTED,false); root.addView(notice);
        notice.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        ScrollView scroll=new ScrollView(this); scroll.setFillViewport(true);
        pageScroll=scroll;
        content=new LinearLayout(this); content.setOrientation(LinearLayout.VERTICAL); content.setPadding(0,dp(16),0,dp(24));
        scroll.addView(content); root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        nav=new LinearLayout(this); nav.setGravity(Gravity.CENTER); if(!child) root.addView(nav,new LinearLayout.LayoutParams(-1,dp(56)));
        for(String name:new String[]{"设备","记录","设置"}) {
            TextView item=text(name,16,name.equals(tab)?BRAND:MUTED,name.equals(tab)); item.setGravity(Gravity.CENTER);
            item.setSelected(name.equals(tab)); item.setContentDescription(name+(name.equals(tab)?"，当前页面":""));
            item.setBackground(BrandUi.surface(this,name.equals(tab)?BrandUi.SOFT:BACK,14));
            nav.addView(item,new LinearLayout.LayoutParams(0,-1,1)); item.setOnClickListener(v->{
                if(loading || name.equals(tab)) return;
                rememberScroll(); hideKeyboard(); tab=name; layout();
            });
        }
        fields.clear(); exportMode=null; audioOutput=null;
        if(editing) editor(); else if(tab.equals("设备")) devicePage(); else if(tab.equals("记录")) recordsPage(); else settingsPage();
        if(!settingsSection.isEmpty() || editing) getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_SECURE);
        int position=scrollPositions.getOrDefault(pageKey(),0); scroll.post(()->scroll.scrollTo(0,position));
    }
    private String pageKey() { return editing?"meeting:"+editingId:tab+":"+settingsSection; }
    private void rememberScroll() { if(pageScroll!=null) scrollPositions.put(pageKey(),pageScroll.getScrollY()); }
    @Override protected void navigateBack() {
        if(loading) { notice.setText("正在完成当前操作，请稍候再返回"); return; }
        hideKeyboard();
        if(editing) {
            if(!saveEditQuietly()) return;
            rememberScroll(); editing=false; draft=null; editingId=""; tab="记录"; layout();
        } else if(!settingsSection.isEmpty()) {
            leaveSettings(()-> { rememberScroll(); settingsSection=""; layout(); });
        } else if(!tab.equals("设备")) { rememberScroll(); tab="设备"; layout(); }
        else moveTaskToBack(true);
    }
    private void captureSettingsBaseline() {
        savedFields.clear(); for(var entry:fields.entrySet()) savedFields.put(entry.getKey(),entry.getValue().getText().toString());
        savedExportMode=exportMode==null?0:exportMode.getSelectedItemPosition();
        savedAudioOutput=audioOutput!=null && audioOutput.isChecked();
    }
    private boolean settingsChanged() {
        for(var entry:fields.entrySet()) if(!entry.getValue().getText().toString().equals(savedFields.get(entry.getKey()))) return true;
        return (exportMode!=null && exportMode.getSelectedItemPosition()!=savedExportMode) || (audioOutput!=null && audioOutput.isChecked()!=savedAudioOutput);
    }
    private void leaveSettings(Runnable destination) {
        if(!settingsChanged()) { destination.run(); return; }
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("保存修改？")
            .setMessage("当前设置有尚未保存的修改。")
            .setNegativeButton("继续编辑",null).setNeutralButton("不保存",(d,w)->destination.run())
            .setPositiveButton("保存并返回",(d,w)-> { if(saveSettings()) destination.run(); }).create();
        dialog.getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE); dialog.show();
    }
    private void openSettings(String section) { rememberScroll(); settingsSection=section; layout(); }
    private void openVoiceprints() {
        startActivity(new Intent(this,VoiceprintActivity.class).putExtra("parent",tab.equals("设置")?"设置":"设备"));
    }
    private interface Job { String run() throws Exception; }
    private void task(String message,Job job,boolean redraw) {
        if(loading || isFinishing() || isDestroyed()) return; rememberScroll(); loading=true; notice.setText(message);
        enableTree(content,false);
        work.execute(()->{
            String result; try { result=job.run(); } catch(Exception e) { result=safeError(e); }
            String finalResult=result;
            main.post(()-> { loading=false; if(isFinishing() || isDestroyed()) return;
                if(redraw && (settingsSection.isEmpty() || !settingsChanged())) layout(); else enableTree(content,true);
                notice.setText(finalResult); });
        });
    }
    private void enableTree(View view,boolean enabled) { view.setEnabled(enabled); if(view instanceof ViewGroup group) for(int i=0;i<group.getChildCount();i++) enableTree(group.getChildAt(i),enabled); }
    private String safeError(Exception error) {
        String value=error.getMessage(); if(value==null) return "操作未完成，请检查配置后重试";
        for(Iterator<String> it=settings.keys();it.hasNext();) {
            String key=it.next(), secret=settings.optString(key);
            if((key.contains("secret") || key.contains("token") || key.contains("password") || key.equals("ak_id")) && secret.length()>=4) value=value.replace(secret,"[已隐藏]");
        }
        if(value.contains("https://") || value.contains("Bearer ") || value.length()>240) return "网络请求未完成，请检查账号权限和网络";
        return value;
    }
    private JSONObject cmd(String name) throws JSONException { return new JSONObject().put("cmd",name); }
    private void devicePage() {
        heading(ble.connected()?"AI通行证已连接":"连接你的AI通行证");
        if(!ble.connected()) note("长按AI通行证上键打开蓝牙，再搜索并连接。");
        if(!ble.connected()) {
            button("搜索附近设备",this::scan,true);
            deviceList=new LinearLayout(this); deviceList.setOrientation(LinearLayout.VERTICAL); content.addView(deviceList);
            found.clear();
        } else {
            note(device.optBoolean("wifi")?"设备 Wi-Fi 已连接":"设备尚未连上 Wi-Fi");
            note(device.optBoolean("configured")?"听悟服务已配置":"前往「设置 → 听悟服务」填写账号，再发送给AI通行证。");
            note(device.optBoolean("audio_output_supported")?(device.optBoolean("audio_output_enabled")?"会后识别用音频已开启 · 之后的新会议可取样":"会后识别用音频未开启 · 可在录音偏好设置"):"当前固件未提供会后识别用音频开关");
            button("同步会议记录",()->task("正在同步会议记录…",()-> { syncRecords(); return "同步完成，可在「记录」查看纪要"; },true),true);
            button("配置 Wi-Fi",this::wifiDialog,false);
            button("发送设置给AI通行证",()->task("正在保存设备设置…",()-> { configureDevice(); return "AI通行证已保存设置"; },true),false);
            button("开始录音",()->new AlertDialog.Builder(this).setTitle("开始会议录音")
                .setMessage("设备将通过 Wi-Fi 上传音频到你配置的听悟账号。录音期间蓝牙会断开，按设备确定键结束录音。")
                .setNegativeButton("取消",null).setPositiveButton("开始",(d,w)->task("正在启动录音…",()->{ble.command(cmd("start")); return "开始指令已发送，请查看AI通行证屏幕";},true)).show(),false);
        }
        line(); heading("录完再整理");
        note("按AI通行证确定键开始或结束录音。上传结束后，连接手机同步纪要，再到「记录」查看、编辑和发布。");
        note("设备与手机均不保存录音文件。开启会后声纹识别用音频后，听悟会生成云端录音供手机取样。手机保存会议文字和编辑稿。");
        button("声纹管理",this::openVoiceprints,false);
    }
    @android.annotation.SuppressLint("MissingPermission") // Checked immediately before starting scan and guarded on each result.
    private void scan() {
        if(Build.VERSION.SDK_INT>=31) {
            if(checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN)!=PackageManager.PERMISSION_GRANTED || checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)!=PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{Manifest.permission.BLUETOOTH_SCAN,Manifest.permission.BLUETOOTH_CONNECT},10); return;
            }
        } else if(checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)!=PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION},10); return;
        }
        try {
            found.clear(); deviceList.removeAllViews(); notice.setText("正在搜索设备…");
            ble.scan(d->{
                if(Build.VERSION.SDK_INT>=31 && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)!=PackageManager.PERMISSION_GRANTED) return;
                if(!tab.equals("设备") || editing || deviceList==null || found.containsKey(d.getAddress())) return;
                found.put(d.getAddress(),d); Button b=new Button(this);
                b.setText(d.getName()==null?"FoloToy AI通行证":d.getName()); BrandUi.button(this,b,false); deviceList.addView(b,new LinearLayout.LayoutParams(-1,-2));
                b.setOnClickListener(v->task("连接中；首次配对请在系统提示中输入设备屏幕上的配对码…",()-> {
                    ble.connect(d); syncRecords(); return "连接完成";
                },true));
            },message->notice.setText(message));
        } catch(Exception e) { notice.setText(safeError(e)); }
    }
    @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] results) {
        super.onRequestPermissionsResult(request,permissions,results);
        if(request==10 && results.length>0 && Arrays.stream(results).allMatch(v->v==PackageManager.PERMISSION_GRANTED)) scan();
        else notice.setText("需要允许附近设备权限才能配置AI通行证");
    }
    private void wifiDialog() {
        provisionReturnDevice=ble.connectedDevice();
        task("正在开启安全蓝牙配网…",()-> {
            JSONObject session=ble.command(cmd("provision"));
            Intent intent=new Intent(this,WifiProvisionActivity.class)
                .putExtra("address",session.getString("address"))
                .putExtra("uuid",session.getString("uuid"))
                .putExtra("name",session.getString("name"))
                .putExtra("pop",session.getString("pop"));
            ble.close();
            main.post(()->startActivityForResult(intent,31));
            return "已进入蓝牙配网";
        },true);
    }
    @Override protected void onActivityResult(int request,int result,Intent data) {
        super.onActivityResult(request,result,data);
        if(request==32) {
            if(result==RESULT_OK && data!=null && draft!=null && editingId.equals(data.getStringExtra("task_id"))) {
                try {
                    if(draftSave!=null) main.removeCallbacks(draftSave);
                    MeetingDraft next=SpeakerSamples.apply(draft,new JSONObject(data.getStringExtra("names")),data.getStringExtra("fingerprint"));
                    vault.save("meeting_"+editingId,next.data); draft=next; layout();
                    notice.setText("已应用确认的姓名。请校对纪要与待办归属；已有飞书文档需重新发布");
                } catch(Exception e) { notice.setText(safeError(e)); }
            }
            return;
        }
        if(request!=31) return;
        String outcome=result==RESULT_OK?"Wi-Fi 已连接并保存":"已退出配网，保留上次保存的网络";
        layout(); notice.setText(outcome+"，正在恢复设备连接…");
        if(provisionReturnDevice!=null) main.postDelayed(()->task("正在恢复设备连接…",()-> {
            ble.connect(provisionReturnDevice); syncRecords(); return outcome;
        },true),3500);
    }
    private void configureDevice() throws Exception {
        JSONObject config=new JSONObject();
        for(String key:new String[]{"ak_id","ak_secret","app_key","title","export_mode","feishu_app_id","feishu_app_secret","folder_token"})
            if(settings.has(key)) config.put(key,settings.get(key));
        int minutes=Integer.parseInt(settings.optString("max_minutes","120"));
        if(minutes<1 || minutes>1440) throw new IllegalArgumentException("录音上限应为 1 至 1440 分钟");
        config.put("max_seconds",minutes*60).put("audio_output_enabled",settings.optBoolean("audio_output_enabled"));
        if(settings.optBoolean("audio_output_enabled") && !device.optBoolean("audio_output_supported"))
            throw new IllegalStateException("请先更新AI通行证固件，再开启会后识别用音频");
        ble.command(cmd("configure").put("settings",config));
        device=ble.command(cmd("status"));
        if(device.optBoolean("audio_output_supported") && device.optBoolean("audio_output_enabled")!=settings.optBoolean("audio_output_enabled"))
            throw new IllegalStateException("音频开关未通过设备回读核对，请重新发送设置");
    }
    private void saveRecord(JSONObject record) throws Exception {
        String id=record.optString("task_id"); if(!id.matches("[a-f0-9]{32}")) return;
        MeetingDraft value=new MeetingDraft(vault.load("meeting_"+id)); value.mergeDevice(record);
        if(value.data.has("original") && record.optInt("phase")>=4 && record.optInt("phase")<6 &&
            !record.optString("export_mode").equals("auto")) {
            try { ble.command(cmd("result_ready").put("task_id",id)); record.put("phase",6); }
            catch(Exception ignored) {}
        }
        String document=value.data.optString("document_id");
        if(!document.isBlank() && value.data.has("claim")) {
            try {
                if(!value.data.optBoolean("device_ack")) {
                    ble.command(cmd("document").put("task_id",id).put("claim",value.data.getString("claim")).put("document_id",document));
                    value.data.put("device_ack",true);
                }
                if(value.data.optString("publish_state").equals("verified") && record.optInt("export_stage")!=5)
                    ble.command(cmd("published").put("task_id",id).put("document_id",document));
            } catch(Exception ignored) {} // Keep the known document and repair on the next sync.
        }
        vault.save("meeting_"+id,value.data);
        index.put(id,new JSONObject().put("title",value.data.optString("title")).put("created_at",record.optLong("created_at"))
            .put("phase",record.optInt("phase")).put("complete",record.optBoolean("complete")));
    }
    private void syncRecords() throws Exception {
        device=ble.command(cmd("status")); saveRecord(device);
        for(int i=0;i<8;i++) saveRecord(ble.command(cmd("record").put("index",i)));
        vault.save("index",index);
        try { installDeviceGrant(); } catch(Exception ignored) {} // Retain encrypted pending grant for a later idle sync.
    }
    private void installDeviceGrant() throws Exception {
        JSONObject pending=vault.load("feishu_device_pending");
        if(pending.optString("refresh_token").isBlank()) return;
        if(!pending.optString("client_id").equals(settings.optString("feishu_app_id")))
            throw new IllegalStateException("设备授权所属应用与当前配置不同");
        JSONObject config=new JSONObject().put("feishu_app_id",settings.getString("feishu_app_id"))
            .put("feishu_app_secret",settings.getString("feishu_app_secret")).put("feishu_refresh_token",pending.getString("refresh_token"));
        ble.command(cmd("configure").put("settings",config));
        vault.save("feishu_device_pending",new JSONObject());
    }
    private void recordsPage() {
        note("原稿与编辑稿分别保存。云端更新不会覆盖你修改的文字。");
        List<String> ids=new ArrayList<>(); index.keys().forEachRemaining(ids::add);
        ids.sort((a,b)->Long.compare(index.optJSONObject(b).optLong("created_at"),index.optJSONObject(a).optLong("created_at")));
        if(ids.isEmpty()) { heading("还没有会议记录"); note("结束录音后，在「设备」连接AI通行证并点击「同步会议记录」。"); }
        for(String id:ids) {
            JSONObject record=index.optJSONObject(id); String title=record.optString("title","会议纪要");
            button(title,()->openRecord(id),false);
            int phase=record.optInt("phase"); note(phase==6 ? (record.optBoolean("complete")?"纪要已生成":"上传不完整，结果仅供核对") : phase==7?"录音或云端处理未完成，可进入查看":"云端处理中，可进入查询");
        }
    }
    private void openRecord(String id) { rememberScroll(); task("正在打开纪要…",()-> {
        draft=new MeetingDraft(vault.load("meeting_"+id)); editingId=id; editing=true; return "";
    },true); }
    private EditText editorField(String label,String value,int lines) {
        content.addView(text(label,14,MUTED,true)); EditText edit=new EditText(this); edit.setText(value); edit.setTextSize(17); edit.setTextColor(INK);
        edit.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE|android.text.InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        edit.setGravity(Gravity.TOP); edit.setMinLines(lines); edit.setPadding(0,dp(8),0,dp(12)); content.addView(edit,new LinearLayout.LayoutParams(-1,-2));
        return edit;
    }
    private void editor() {
        if(draft==null) return;
        JSONObject state=draft.data.optJSONObject("device");
        if(state!=null && !state.optBoolean("complete")) note("这场录音上传不完整。请核对内容后再发布。");
        titleEditor=editorField("会议标题",draft.data.optString("title"),1);
        bodyEditor=editorField("纪要与待办",draft.data.optString("body"),8);
        button(draft.data.has("original")?"重新获取云端原稿":"获取最终纪要",()->{
            if(!saveEditQuietly()) return; task("正在从听悟获取最终结果…",()-> {
                draft.importResult(new Tingwu(settings).results(editingId)); vault.save("meeting_"+editingId,draft.data);
                if(ble.connected()) {
                    try { ble.command(cmd("result_ready").put("task_id",editingId)); }
                    catch(Exception ignored) {} // Cloud result is already safely saved.
                }
                return "原稿已保存，你已有的编辑内容保持不变";
            },true);
        },false);
        button("查看云端原稿",()->new AlertDialog.Builder(this).setTitle("云端原稿")
            .setMessage(draft.originalBody().isBlank()?"尚未获取到摘要":draft.originalBody()).setPositiveButton("关闭",null).show(),false);
        line(); heading("发言人与逐字稿");
        note("改名只应用于本场会议，不代表已注册声纹。修改逐字稿后，摘要需要你另行校对。");
        button("识别本场发言人",()-> {
            if(!saveEditQuietly()) return; rememberScroll(); hideKeyboard();
            if(draftSave!=null) main.removeCallbacks(draftSave);
            startActivityForResult(new Intent(this,MeetingSpeakerActivity.class).putExtra("task_id",editingId),32);
        },false);
        for(String speaker:draft.speakerIds()) {
            JSONObject names=draft.data.optJSONObject("speakers"); String name=names==null?"":names.optString(speaker);
            button("发言人 "+speaker+(name.isBlank()?"":" · "+name),()->renameSpeaker(speaker),false);
        }
        transcriptEditor=editorField("逐字稿",draft.data.optString("transcript"),4);
        note("发布到飞书时会包含逐字稿，并自动分章节、分发言人排版。原稿可对应的时间段也会一并显示。");
        button("预览飞书排版",()-> {
            if(!saveEditQuietly()) return;
            try {
                ScrollView scroll=new ScrollView(this); LinearLayout page=new LinearLayout(this); page.setOrientation(LinearLayout.VERTICAL);
                page.setPadding(dp(24),dp(12),dp(24),dp(20)); scroll.addView(page);
                JSONArray blocks=MeetingDocument.build(draft);
                // A bounded native preview; the full text is always exported to Feishu.
                for(int i=0;i<Math.min(120,blocks.length());i++) {
                    JSONObject block=blocks.getJSONObject(i); int type=block.optInt("block_type");
                    String value=Feishu.textOf(block),key=MeetingDocument.key(type);
                    JSONObject style=block.getJSONObject(key).getJSONArray("elements").getJSONObject(0).getJSONObject("text_run").optJSONObject("text_element_style");
                    TextView row=text((type==12?"• ":"")+value,type==4?21:16,type==15?MUTED:INK,type==4 || (style!=null && style.optBoolean("bold")));
                    row.setPadding(0,dp(type==4?18:6),0,dp(6)); page.addView(row);
                }
                if(blocks.length()>120) page.addView(text("预览显示前 120 段，发布时包含全部内容。",14,MUTED,false));
                AlertDialog dialog=new AlertDialog.Builder(this).setTitle(draft.data.optString("title")).setView(scroll).setPositiveButton("关闭",null).create();
                dialog.getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE); dialog.show();
            }
            catch(Exception e) { notice.setText(safeError(e)); }
        },false);
        button("保存编辑稿",()-> { if(saveEditQuietly()) notice.setText("编辑稿已保存到手机"); },false);
        button(draft.data.optString("document_id").isBlank()?"生成飞书文档":"更新这份飞书文档",()->publishDialog(),true);
        if(!draft.data.optString("document_id").isBlank()) {
            button("打开飞书文档",()->openUrl("https://open.feishu.cn/docx/"+draft.data.optString("document_id")),false);
            button("核对飞书当前版本",()-> { if(!saveEditQuietly()) return; task("正在读取飞书文档…",()-> {
                JSONObject snapshot=feishu.fetch(draft.data.getString("document_id"));
                main.post(()->reviewRemote(snapshot));
                return "已读取飞书版本，请核对后选择如何处理";
            },true); },false);
        }
        TextWatcher watcher=new TextWatcher() {
            public void beforeTextChanged(CharSequence s,int start,int count,int after) {}
            public void onTextChanged(CharSequence s,int start,int before,int count) {
                if(draftSave!=null) main.removeCallbacks(draftSave); draftSave=()->saveEditQuietly(); main.postDelayed(draftSave,500);
            }
            public void afterTextChanged(Editable e) {}
        };
        titleEditor.addTextChangedListener(watcher); bodyEditor.addTextChangedListener(watcher); transcriptEditor.addTextChangedListener(watcher);
    }
    private boolean saveEditQuietly() {
        if(!editing) return true;
        if(loading || draft==null || titleEditor==null) return false;
        try {
            draft.data.put("title",titleEditor.getText().toString());
            // Do not turn an unpopulated editor into a user-authored empty draft.
            if(draft.data.has("original") || !bodyEditor.getText().toString().isEmpty()) draft.data.put("body",bodyEditor.getText().toString());
            if(draft.data.has("original") || !transcriptEditor.getText().toString().isEmpty()) draft.data.put("transcript",transcriptEditor.getText().toString());
            vault.save("meeting_"+editingId,draft.data);
            JSONObject item=index.optJSONObject(editingId); if(item!=null) item.put("title",draft.data.optString("title")); vault.save("index",index);
            return true;
        } catch(Exception e) { notice.setText("编辑稿未能保存，请重试后再返回"); return false; }
    }
    private void renameSpeaker(String id) {
        if(!saveEditQuietly()) return; rememberScroll(); EditText input=new EditText(this); input.setSingleLine(); input.setHint("姓名或称呼");
        new AlertDialog.Builder(this).setTitle("设置本场发言人姓名").setView(input).setNegativeButton("取消",null)
            .setPositiveButton("保存",(d,w)-> {
                try {
                    JSONObject names=draft.data.optJSONObject("speakers"); if(names==null) names=new JSONObject();
                    String old=names.optString(id,"发言人 "+id); if(old.isBlank()) old="发言人 "+id;
                    String next=input.getText().toString().strip(); if(next.isEmpty() || next.length()>40) throw new IllegalArgumentException("姓名应为 1 至 40 个字符");
                    String transcript=draft.data.optString("transcript");
                    // Rename paragraph labels only; preserve corrected words in the transcript.
                    transcript=transcript.replaceAll("(?m)^"+java.util.regex.Pattern.quote(old+"："),java.util.regex.Matcher.quoteReplacement(next+"："));
                    names.put(id,next); draft.data.put("speakers",names).put("transcript",transcript); vault.save("meeting_"+editingId,draft.data); layout();
                } catch(Exception e) { notice.setText(safeError(e)); }
            }).show();
    }
    private void publishDialog() {
        if(!saveEditQuietly()) return;
        new AlertDialog.Builder(this).setTitle("发布到飞书")
            .setMessage("将发布当前标题、纪要与待办，以及完整逐字稿，自动按章节和发言人排版。已有文档会更新同一份；若飞书内容已变更，将停止更新供你核对。")
            .setNegativeButton("取消",null).setPositiveButton("发布",(d,w)->task("正在保存到飞书…",this::publish,true)).show();
    }
    private void reviewRemote(JSONObject snapshot) {
        if(isDestroyed() || draft==null) return;
        StringBuilder body=new StringBuilder(); JSONArray items=snapshot.optJSONArray("items");
        if(items!=null) for(int i=0;i<items.length();i++) body.append(Feishu.textOf(items.optJSONObject(i))).append('\n');
        new AlertDialog.Builder(this).setTitle("核对飞书当前内容")
            .setMessage(snapshot.optString("title")+"\n\n"+body+"\n\n允许更新后，仍需点击发布。飞书再次发生变化会停止写入。")
            .setNegativeButton("保留，暂不更新",null)
            .setPositiveButton("允许用编辑稿更新",(dialog,which)-> {
                try { feishu.acceptCurrent(draft.data.getString("document_id")); draft.data.put("remote",snapshot); vault.save("meeting_"+editingId,draft.data); notice.setText("已确认此版本；点击更新文档后才会写入"); }
                catch(Exception e) { notice.setText(safeError(e)); }
            }).show();
    }
    private String publish() throws Exception {
        String title=draft.data.optString("title").strip(), body=draft.data.optString("body").strip();
        String transcript=draft.data.optString("transcript").strip();
        if(title.isEmpty() || title.length()>160 || (body.isEmpty() && transcript.isEmpty()) || body.length()>50000 || transcript.length()>500000)
            throw new IllegalArgumentException("请填写标题和会议内容；标题最多 160 字，纪要 5 万字，逐字稿 50 万字");
        JSONArray blocks=MeetingDocument.build(draft);
        String export=title+"\n"+MeetingDocument.plain(blocks);
        if(export.matches("(?s).*\\bBearer\\s+\\S+.*") || export.contains("-----BEGIN ") || export.matches("(?s).*\\bLTAI[A-Za-z0-9]{12,}.*")) throw new IllegalArgumentException("正文中可能含有凭证，请先移除");
        for(String key:new String[]{"ak_id","ak_secret","feishu_app_secret"}) {
            String secret=settings.optString(key); if(secret.length()>=6 && export.contains(secret)) throw new IllegalArgumentException("正文包含已配置的凭证，请先移除");
        }
        String id=draft.data.optString("document_id");
        if(id.isBlank()) {
            feishu.accessToken(); // Fail before reserving a creation if authorization is missing.
            String state=draft.data.optString("publish_state");
            if(!state.isBlank() && !state.equals("claiming")) throw new IllegalStateException("上次创建结果需要核对，请先找到已有文档，避免重复创建");
            if(state.isBlank()) {
                draft.data.put("claim",UUID.randomUUID().toString().replace("-","")).put("publish_state","claiming");
                vault.save("meeting_"+editingId,draft.data);
            }
            JSONObject claim=ble.command(cmd("claim").put("task_id",editingId).put("claim",draft.data.getString("claim")));
            draft.data.put("claim",claim.getString("claim")).put("publish_state","creating"); vault.save("meeting_"+editingId,draft.data);
            id=feishu.create(title,settings.optString("folder_token"));
            draft.data.put("document_id",id).put("publish_state","created"); vault.save("meeting_"+editingId,draft.data);
        }
        if(draft.data.has("claim") && !draft.data.optBoolean("device_ack") && ble.connected()) {
            ble.command(cmd("document").put("task_id",editingId).put("claim",draft.data.getString("claim")).put("document_id",id));
            draft.data.put("device_ack",true); vault.save("meeting_"+editingId,draft.data);
        }
        if(!draft.data.has("remote") && draft.data.optString("publish_state").equals("created")) {
            JSONObject remote=feishu.fetch(id);
            if(remote.getJSONArray("items").length()!=0) throw new IllegalStateException("文档已有内容，请先核对飞书当前版本");
            draft.data.put("remote",remote); vault.save("meeting_"+editingId,draft.data);
        }
        JSONObject before=draft.data.optJSONObject("remote");
        if(before==null) throw new IllegalStateException("请先点击“核对飞书当前版本”，再更新这份文档");
        draft.data.put("publish_state","writing"); vault.save("meeting_"+editingId,draft.data);
        JSONObject verified=feishu.write(id,title,blocks,before);
        draft.data.put("remote",verified).put("publish_state","verified"); vault.save("meeting_"+editingId,draft.data);
        if(ble.connected()) {
            try { ble.command(cmd("published").put("task_id",editingId).put("document_id",id)); }
            catch(Exception ignored) { return "飞书已验证保存；设备状态待下次同步确认"; }
        }
        return "飞书文档已保存，内容已核对一致";
    }
    private void settingsPage() {
        if(settingsSection.isEmpty()) {
            note("按需要配置服务，连接AI通行证后发送设置即可生效。");
            button("听悟服务  ›",()->openSettings("听悟服务"),false); note("语音转写、发言人分离与 AI 纪要");
            button("录音偏好  ›",()->openSettings("录音偏好"),false); note("默认标题、录音时长与发布方式");
            button("飞书连接  ›",()->openSettings("飞书连接"),false); note("授权账号，将纪要保存为飞书文档");
            button("声纹管理  ›",this::openVoiceprints,false); note("讯飞声纹服务 · 录入声音并测试识别");
            line(); note("云服务使用你自己的账号，费用与试用额度以各服务控制台为准。");
            return;
        }
        if(settingsSection.equals("听悟服务")) {
            note("填写阿里云听悟凭证，用于录音转写和生成纪要。");
            field("AccessKey ID","ak_id",true,""); field("AccessKey Secret","ak_secret",true,""); field("听悟 AppKey","app_key",false,"");
            button("打开听悟控制台",()->openUrl("https://tingwu.console.aliyun.com/"),false);
        } else if(settingsSection.equals("录音偏好")) {
            field("默认标题（留空使用日期时间）","title",false,"");
            EditText minutes=field("单场录音上限（分钟）","max_minutes",false,"120"); minutes.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
            note("可设为 1–1440 分钟。实际录音时长还取决于设备电量和网络；长时间录音仍在验证中。");
            content.addView(text("纪要发布方式",14,MUTED,true)); space(8);
            exportMode=new Spinner(this); exportMode.setContentDescription("纪要发布方式"); exportMode.setMinimumHeight(dp(52));
            exportMode.setAdapter(new ArrayAdapter<>(this,android.R.layout.simple_spinner_dropdown_item,new String[]{"手机确认后发布","AI通行证自动保存到飞书"}));
            exportMode.setSelection(settings.optString("export_mode").equals("auto")?1:0); content.addView(exportMode); space(16);
            note("自动保存需要先在「飞书连接」中授权AI通行证。");
            line();
            audioOutput=new CheckBox(this); audioOutput.setText("会后声纹识别用音频"); audioOutput.setTextColor(INK); audioOutput.setTextSize(16); audioOutput.setMinHeight(dp(52));
            audioOutput.setButtonTintList(android.content.res.ColorStateList.valueOf(BrandUi.PRIMARY));
            audioOutput.setChecked(settings.optBoolean("audio_output_enabled")); content.addView(audioOutput);
            note("默认关闭。开启并发送给AI通行证后，新会议会在听悟生成 MP3，供手机提取发言片段；声纹识别仍需你在会议编辑页点击启动。");
            note("听悟云端录音的保留期限需向阿里云确认。当前公开 API 没有录音删除接口，App 无法代删；关闭开关只影响新会议，不会删除已有云端录音。手机取样完成或取消后清空音频内存。");
            note("云端音频输出和声纹查询可能消耗试用或付费额度，以听悟、讯飞控制台为准。选择手机确认后发布，便于先核对发言人姓名。");
        } else if(settingsSection.equals("飞书连接")) {
            try {
                JSONObject grant=vault.load("feishu_phone");
                note(grant.optString("access_token").isBlank()?"手机尚未授权飞书":
                    grant.optString("refresh_token").isBlank()?"手机已授权，到期后需重新登录":"手机已授权飞书");
            } catch(Exception e) { note("无法读取手机授权，请重试"); }
            field("飞书应用 App ID","feishu_app_id",false,""); field("飞书应用 App Secret","feishu_app_secret",true,""); field("目标文件夹 Token（可选）","folder_token",false,"");
            note("使用飞书自建应用，需开通文档和云空间权限。手机与AI通行证分别授权。");
        }
        button("保存设置",()->saveSettings(),true);
        if(settingsSection.equals("飞书连接")) {
            button("授权手机访问飞书",()->authorize(false),false);
            button("授权AI通行证自动保存",()->authorize(true),false);
        }
        button("保存并发送给AI通行证",()-> {
            if(!ble.connected()) { notice.setText("请先保存设置，再到「设备」连接AI通行证"); return; }
            if(saveSettings()) task("正在发送设置…",()-> { configureDevice(); return "AI通行证已保存设置"; },true);
        },false);
        captureSettingsBaseline();
    }
    private boolean saveSettings() {
        try {
            JSONObject next=new JSONObject(settings.toString());
            for(var entry:fields.entrySet()) next.put(entry.getKey(),entry.getValue().getText().toString().strip());
            if(exportMode!=null) next.put("export_mode",exportMode.getSelectedItemPosition()==1?"auto":"phone");
            if(audioOutput!=null) next.put("audio_output_enabled",audioOutput.isChecked());
            int minutes=Integer.parseInt(next.optString("max_minutes","120"));
            if(minutes<1 || minutes>1440) throw new IllegalArgumentException("时长应为 1 至 1440 分钟");
            if(!next.optString("feishu_app_id").equals(settings.optString("feishu_app_id"))) vault.save("feishu_phone",new JSONObject());
            vault.save("settings",next); settings=next; captureSettingsBaseline(); notice.setText("设置已保存到手机，可发送给AI通行证生效"); return true;
        } catch(Exception e) { notice.setText(safeError(e)); return false; }
    }
    private void authorize(boolean hardware) {
        if(authRunning || !saveSettings()) return;
        if(hardware && !ble.connected()) { notice.setText("请先连接设备，再授权独立归档"); return; }
        task("正在获取飞书授权入口…",()-> {
            JSONObject authorization=feishu.beginAuthorization(settings);
            String url=authorization.optString("verification_uri_complete",authorization.optString("verification_uri"));
            URIValidator.feishuAuthorization(url);
            long deadline=System.currentTimeMillis()+authorization.optLong("expires_in",240)*1000;
            vault.save("feishu_pending",new JSONObject().put("authorization",authorization).put("hardware",hardware)
                .put("deadline",deadline).put("client_id",settings.optString("feishu_app_id")));
            main.post(()-> { authRunning=true; openUrl(url); pollAuth(authorization,hardware,deadline); });
            return "请在飞书页面完成授权，完成后返回 App";
        },false);
    }
    private void resumeAuthorization() {
        if(authRunning) return;
        try {
            JSONObject pending=vault.load("feishu_pending");
            if(pending.optLong("deadline")>System.currentTimeMillis() && pending.has("authorization") &&
                pending.optString("client_id").equals(settings.optString("feishu_app_id"))) {
                authRunning=true; pollAuth(pending.getJSONObject("authorization"),pending.optBoolean("hardware"),pending.getLong("deadline"));
                notice.setText("正在接收飞书授权结果…");
            }
        } catch(Exception e) { notice.setText(safeError(e)); }
    }
    private void pollAuth(JSONObject authorization,boolean hardware,long deadline) {
        int delay=Math.max(5,authorization.optInt("interval",5));
        main.postDelayed(()->work.execute(()-> {
            try {
                if(!authRunning || isDestroyed()) return;
                if(System.currentTimeMillis()>=deadline) { vault.save("feishu_pending",new JSONObject()); throw new IllegalStateException("授权已过期，请重新授权"); }
                JSONObject tokens=feishu.pollAuthorization(settings,authorization);
                if(tokens.has("error")) {
                    String error=tokens.optString("error");
                    if(!error.equals("authorization_pending") && !error.equals("slow_down")) {
                        vault.save("feishu_pending",new JSONObject());
                        throw new IllegalStateException("飞书授权未完成（"+error.replaceAll("[^a-z_]","")+"），请重新授权");
                    }
                    if(error.equals("slow_down")) authorization.put("interval",Math.min(60,delay+5));
                    main.post(()->pollAuth(authorization,hardware,deadline)); return;
                }
                if(hardware) {
                    if(tokens.optString("refresh_token").isBlank()) {
                        vault.save("feishu_pending",new JSONObject());
                        throw new IllegalStateException("设备独立归档需要 offline_access 授权，请检查飞书应用权限");
                    }
                    vault.save("feishu_device_pending",new JSONObject().put("client_id",settings.getString("feishu_app_id"))
                        .put("refresh_token",tokens.getString("refresh_token")));
                    vault.save("feishu_pending",new JSONObject());
                    try { installDeviceGrant(); }
                    catch(Exception e) {
                        main.post(()-> { authRunning=false; notice.setText("设备授权已保存到手机，重新连接并同步后写入设备"); }); return;
                    }
                    // Hardware owns this refresh chain. Never save it as the phone's session.
                } else vault.save("feishu_phone",tokens);
                vault.save("feishu_pending",new JSONObject());
                main.post(()-> { authRunning=false; notice.setText(hardware?"设备已保存独立授权":"手机飞书授权完成"); });
            } catch(java.io.IOException e) {
                if((getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE)!=0)
                    android.util.Log.i("FoloAuthStatus",safeError(e));
                // A transient mobile-network failure must not discard an
                // already approved browser authorization. Resume until expiry.
                main.post(()-> { if(authRunning && !isDestroyed()) {
                    notice.setText("正在重试接收授权："+safeError(e)); pollAuth(authorization,hardware,deadline);
                }});
            } catch(Exception e) { main.post(()-> { authRunning=false; notice.setText(safeError(e)); }); }
        }),delay*1000L);
    }
    private void openUrl(String url) { try { startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url))); } catch(Exception e) { notice.setText("手机没有可打开该链接的应用"); } }
    @Override protected void onPause() { saveEditQuietly(); super.onPause(); }
    @Override protected void onResume() { super.onResume(); if(vault!=null && notice!=null) resumeAuthorization(); }
    @Override protected void onDestroy() { authRunning=false; if(draftSave!=null) main.removeCallbacks(draftSave); ble.close(); work.shutdownNow(); super.onDestroy(); }
    private static class URIValidator {
        static void feishuAuthorization(String url) throws Exception {
            java.net.URI uri=java.net.URI.create(url); String host=uri.getHost();
            if(!"https".equals(uri.getScheme()) || host==null || !(host.equals("feishu.cn") || host.endsWith(".feishu.cn")) || uri.getUserInfo()!=null)
                throw new IllegalArgumentException("飞书授权地址验证失败");
        }
    }
}
