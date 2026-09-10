package com.folotoy.meeting;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.*;
import android.text.InputType;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.util.*;
import java.util.concurrent.*;

/** Phone-only experiment. Does not alter Tingwu transcripts, device settings or meeting names. */
public class VoiceprintActivity extends BrandActivity {
    private static final int INK=BrandUi.INK,MUTED=BrandUi.MUTED,BRAND=BrandUi.PRESSED,BACK=BrandUi.BACK;
    // Survives rotation; all credential changes and cloud mutations are serialized across instances.
    static final ExecutorService CLOUD=Executors.newSingleThreadExecutor();
    private final Handler main=new Handler(Looper.getMainLooper());
    private Vault vault;
    private JSONObject settings=new JSONObject(),state=new JSONObject();
    private LinearLayout content;
    private TextView notice,timer;
    private ProgressBar level;
    private EditText nameInput,appIdInput,keyInput,secretInput;
    private String message="",personName="",result="";
    private boolean busy=true,configuring,foreground,enrolling;
    private VoiceCapture capture;
    private byte[] sample;
    private JSONObject configDraft;

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved); vault=new Vault(getApplicationContext()); draw();
        job("正在读取声纹配置…",()->"",true);
    }
    private interface Job { String run() throws Exception; }
    private Voiceprints service() throws Exception {
        VoiceprintClient.Credentials credentials=VoiceprintClient.Credentials.from(settings);
        Voiceprints.Store store=new Voiceprints.Store() {
            public JSONObject load(String key) throws Exception { return vault.load(key); }
            public void save(String key,JSONObject value) throws Exception { vault.save(key,value); }
        };
        return new Voiceprints(store,VoiceprintClient.live(credentials),credentials.appId);
    }
    private void job(String progress,Job task,boolean initial) {
        if(busy && !initial) return;
        if(!configuring && nameInput!=null) personName=nameInput.getText().toString();
        busy=true; message=progress; draw();
        CLOUD.execute(()-> {
            String outcome;
            try { outcome=task.run(); } catch(Exception e) { outcome=safeError(e); }
            JSONObject nextSettings=settings,nextState=state;
            try {
                nextSettings=vault.load("voice_settings");
                nextState=nextSettings.optString("app_id").isBlank()?new JSONObject():vault.load(Voiceprints.accountKey(nextSettings.getString("app_id")));
            } catch(Exception e) { outcome="本地声纹资料读取失败，原记录已保留。请退出后重试"; }
            String finalOutcome=outcome; JSONObject finalSettings=nextSettings,finalState=nextState;
            main.post(()-> {
                if(isDestroyed() || isFinishing()) return;
                settings=finalSettings; state=finalState; busy=false; message=finalOutcome; draw();
            });
        });
    }
    private String safeError(Exception e) {
        // Only expose errors written by our adapter. Never display response bodies or signed URLs.
        String text=e.getMessage();
        if(!(e instanceof java.io.IOException || e instanceof IllegalArgumentException || e instanceof IllegalStateException) ||
                text==null || text.length()>160 || text.contains("http") || text.contains("api_key") || text.contains("\n"))
            return "操作未完成，请检查配置和网络，并核对云端状态";
        for(String key:new String[]{"api_key","api_secret"}) {
            String secret=settings.optString(key); if(!secret.isEmpty()) text=text.replace(secret,"[已隐藏]");
        }
        return text;
    }
    private int dp(int value) { return Math.round(value*getResources().getDisplayMetrics().density); }
    private TextView text(String value,int size,int color,boolean bold) {
        TextView view=new TextView(this); view.setText(value); view.setTextSize(size); view.setTextColor(color);
        view.setLineSpacing(dp(3),1.1f); if(bold) view.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL)); return view;
    }
    private void space(int height) { content.addView(new View(this),new LinearLayout.LayoutParams(1,dp(height))); }
    private void note(String value) { content.addView(text(value,14,MUTED,false)); space(12); }
    private void heading(String value) { space(12); content.addView(text(value,21,INK,true)); space(10); }
    private Button button(String label,Runnable action,boolean primary,boolean enabled) {
        Button button=new Button(this); button.setText(label); BrandUi.button(this,button,primary);
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,-2); params.setMargins(0,dp(4),0,dp(8)); content.addView(button,params);
        button.setEnabled(enabled); button.setOnClickListener(v->action.run()); return button;
    }
    private EditText field(String label,String value,boolean secret) {
        TextView title=text(label,14,MUTED,true); content.addView(title);
        EditText input=new EditText(this); input.setId(View.generateViewId()); title.setLabelFor(input.getId());
        input.setTextSize(16); input.setTextColor(INK); input.setSingleLine(); input.setMinHeight(dp(52)); input.setSaveEnabled(false);
        input.setInputType(InputType.TYPE_CLASS_TEXT|(secret?InputType.TYPE_TEXT_VARIATION_PASSWORD:InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS));
        input.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO); input.setText(value); input.setEnabled(!busy);
        content.addView(input,new LinearLayout.LayoutParams(-1,-2)); space(12); return input;
    }
    private void draw() {
        if(isDestroyed() || isFinishing()) return;
        if(configuring && appIdInput!=null) configDraft=currentConfig();
        if(configuring) getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_SECURE);
        LinearLayout root=new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setBackgroundColor(BACK);
        root.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
        root.setPadding(dp(20),dp(12),dp(20),dp(12));
        root.setOnApplyWindowInsetsListener((v,insets)-> {
            if(Build.VERSION.SDK_INT>=30) {
                android.graphics.Insets bars=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.ime());
                root.setPadding(dp(20)+bars.left,dp(12)+bars.top,dp(20)+bars.right,dp(12)+bars.bottom);
            } else root.setPadding(dp(20)+insets.getSystemWindowInsetLeft(),dp(12)+insets.getSystemWindowInsetTop(),dp(20)+insets.getSystemWindowInsetRight(),dp(12)+insets.getSystemWindowInsetBottom());
            return insets;
        });
        setContentView(root);
        boolean child=configuring || capture!=null || sample!=null;
        String parent="设置".equals(getIntent().getStringExtra("parent"))?"设置":"设备";
        root.addView(BrandUi.back(this,child?"返回声纹管理":"返回"+parent,this::navigateBack));
        root.addView(text(configuring?"讯飞声纹设置":capture!=null || sample!=null?(enrolling?"录入声纹":"识别声音"):"声纹管理",28,INK,true));
        notice=text(message,14,BRAND,false); notice.setPadding(0,dp(8),0,dp(8)); notice.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE); root.addView(notice);
        ScrollView scroll=new ScrollView(this); scroll.setFillViewport(true); root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        content=new LinearLayout(this); content.setOrientation(LinearLayout.VERTICAL); content.setPadding(0,dp(4),0,dp(24)); scroll.addView(content);
        if(configuring) configPage(); else trialPage();
    }
    private void configPage() {
        note("填写讯飞「声纹识别（新版）」凭证，用于识别已录入的声音。设置加密保存在手机。");
        JSONObject values=configDraft==null?settings:configDraft;
        appIdInput=field("AppID",values.optString("app_id"),false);
        secretInput=field("APISecret",values.optString("api_secret"),true);
        keyInput=field("APIKey",values.optString("api_key"),true);
        note("按控制台原样复制，保留大小写；APISecret 和 APIKey 是两个不同字段。");
        button("保存设置",this::saveConfig,true,!busy);
        note("更换 AppID 会切换声纹库，原账号记录会保留。卸载 App 不会删除云端声纹，请先在声纹管理中删除。");
        pricing();
    }
    private void pricing() {
        heading("试用与费用");
        note("讯飞声纹服务需单独开通。免费额度、有效期和费用以讯飞控制台为准，App 暂不显示剩余额度。");
        note("只有点击准备、上传、核对或删除时才请求云服务。保存配置和录音本身不请求云服务。请先领取免费包；领取及后续付费均在讯飞办理。");
        button("查看声纹服务与免费包",()->open("https://www.xfyun.cn/services/voiceprint-recognition"),false,!busy);
        button("打开讯飞控制台",()->open("https://console.xfyun.cn/"),false,!busy);
    }
    private void trialPage() {
        if(capture!=null || sample!=null) { capturePage(); return; }
        boolean configured=!settings.optString("app_id").isBlank(), ready=state.optBoolean("ready");
        note("先录入声音，再换句话测试识别。会议录完后，可在「编辑纪要 → 识别本场发言人」取样匹配，确认姓名后应用到本场纪要。");
        if(!result.isEmpty()) { heading("上次识别结果"); note(result); }
        heading("1  连接声纹服务");
        note(!configured?"尚未配置讯飞声纹服务":ready?"声纹库已准备好 · 已录入 "+Voiceprints.readyCount(state)+" 人":"配置已保存，声纹库尚待准备或核对");
        button(configured?"讯飞声纹设置  ›":"配置讯飞声纹服务",()-> {
            if(nameInput!=null) personName=nameInput.getText().toString();
            configuring=true; appIdInput=null; configDraft=null; message=""; draw();
        },!configured,!busy);
        if(configured) button("检查连接和账号",()->job("正在检查连接和账号…",()-> {
            service().checkConnection(); return "连接成功，账号验证通过";
        },false),false,!busy);
        if(configured) button(ready?"核对云端声纹状态":"准备声纹库",()->job("正在准备 / 核对声纹库…",()-> {
            service().prepare(); return "声纹库已连接；待核对记录请查看下方状态";
        },false),!ready,!busy);
        heading("2  录入一位发言人");
        note("请本人在安静处连续说话 5 秒。录音后还需点击上传，声音才会发送到你的讯飞账号建立声纹。");
        nameInput=field("姓名或称呼",personName,false); nameInput.setHint("例如：小王");
        button("录音并录入声纹",()->begin(true),true,!busy && ready);
        heading("3  换句话识别");
        note("用不同内容再说 5 秒，也可以让未录入的人测试。相似度分数不是识别成功率。");
        button("录音并测试识别",()->begin(false),false,!busy && Voiceprints.readyCount(state)>0);
        peoplePage();
        if(!configured) pricing();
        else { space(12); note("录音只暂存在内存，切换到其他应用会清除未上传的录音。已上传的请求会继续完成。识别结果供试用核对。"); }
    }
    private void peoplePage() {
        heading("已录入与待核对");
        JSONObject people=state.optJSONObject("people");
        if(people==null || people.length()==0) { note("还没有声纹。注册成功后会显示在这里。"); return; }
        List<String> ids=new ArrayList<>(); for(Iterator<String> it=people.keys();it.hasNext();) ids.add(it.next()); Collections.sort(ids);
        for(String id:ids) {
            JSONObject person=people.optJSONObject(id); if(person==null) continue;
            String name=person.optString("name"),status=person.optString("state");
            content.addView(text(name,18,INK,true));
            note(status.equals("ready")?"已注册":status.equals("pending_delete")?"删除待确认 · 已暂停参与识别":status.equals("pending_update")?"补录待确认 · 已暂停参与识别，请核对云端状态":"注册待确认 · 请先核对云端状态");
            button(status.equals("pending_delete")?"重试删除 "+name:"删除 "+name,()->new AlertDialog.Builder(this).setTitle("删除声纹")
                    .setMessage("将请求讯飞删除「"+name+"」的声纹特征。确认成功后才移除本机记录；删除后需要重新录入才能识别。")
                    .setNegativeButton("取消",null).setPositiveButton("删除",(dialog,which)->job("正在删除声纹…",()-> {
                        service().delete(id); return "讯飞已确认删除该声纹";
                    },false)).show(),false,!busy);
        }
        note("网络中断可能留下待核对记录。查询接口可能只返回部分结果，App 不会根据“未查到”就认定删除成功。必要时在讯飞控制台核对特征 ID。");
        button("查看声纹库与特征 ID",()-> {
            StringBuilder details=new StringBuilder("Group ID: ").append(state.optString("group_id"));
            for(String id:ids) details.append("\n\n").append(people.optJSONObject(id).optString("name")).append("\nFeature ID: ").append(id);
            new AlertDialog.Builder(this).setTitle("云端核对信息").setMessage(details.toString()).setPositiveButton("关闭",null).show();
        },false,!busy);
    }
    private void begin(boolean enrollment) {
        personName=nameInput==null?personName:nameInput.getText().toString().strip();
        if(enrollment && (personName.isEmpty() || personName.length()>40)) { notice.setText("请填写 1 至 40 个字符的姓名或称呼"); return; }
        if(checkSelfPermission(Manifest.permission.RECORD_AUDIO)!=PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO},40); return;
        }
        enrolling=enrollment; result=""; message=""; capture=new VoiceCapture(); VoiceCapture current=capture; draw();
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        current.start(getApplicationContext(),new VoiceCapture.Listener() {
            public void level(int milliseconds,int percent) { main.post(()-> {
                if(capture!=current || !foreground || isDestroyed()) return;
                timer.setText(String.format(Locale.CHINA,"正在录音  %.1f / 5 秒",milliseconds/1000.0)); level.setProgress(percent);
            }); }
            public void complete(byte[] wav,String problem) { main.post(()-> {
                if(capture!=current || !foreground || isDestroyed()) { if(wav!=null) Arrays.fill(wav,(byte)0); return; }
                getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON); capture=null;
                sample=wav; message=problem; draw();
            }); }
        });
    }
    private void capturePage() {
        heading(enrolling?"录入："+personName:"识别一段新声音");
        if(capture!=null) {
            timer=text("正在录音  0.0 / 5 秒",24,0xffb42318,true); content.addView(timer); space(20);
            level=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal); level.setMax(100); level.setContentDescription("麦克风音量");
            content.addView(level,new LinearLayout.LayoutParams(-1,dp(12))); space(20);
            note(enrolling?"请连续说一句话，例如：今天我想测试手机是否能够记住我的声音。":"请换一句话连续说，例如：明天下午我准备和同事一起讨论新的项目。 ");
            note("请保持只有一人说话，录满 5 秒后自动停止。");
            button("取消录音",()-> { discard(); message="录音已取消，未上传"; draw(); },false,true);
        } else {
            content.addView(text("5 秒录音已就绪",24,INK,true)); space(16);
            note("录音暂存在手机内存，还没有上传。点击下方按钮将发送到你配置的讯飞声纹服务，可能消耗该服务的试用或付费次数。");
            if(enrolling) note("上传即表示这是本人自愿提供的声音，用于建立可跨次识别的云端声纹。可在本页删除声纹。");
            button(enrolling?"上传并注册声纹":"上传并识别",this::upload,true,!busy);
            button("丢弃这段录音",()-> { discard(); message="录音已丢弃，未上传"; draw(); },false,!busy);
        }
    }
    private void upload() {
        if(sample==null || busy) return;
        byte[] wav=sample; sample=null; boolean enrollment=enrolling; String name=personName;
        job(enrollment?"正在上传并注册声纹…":"正在上传并识别…",()-> {
            try {
                if(enrollment) { service().enroll(name,wav); return "已注册「"+name+"」。请换句话测试识别"; }
                Voiceprints.Match match=service().identify(wav);
                String outcome=(match.name.isEmpty()?"暂未认出":"可能是「"+match.name+"」")+
                        (Double.isFinite(match.score)?String.format(Locale.CHINA," · 相似度 %.2f",match.score):"")+"\n"+match.reason;
                main.post(()-> { if(!isDestroyed()) result=outcome; }); return "识别完成";
            } finally { Arrays.fill(wav,(byte)0); }
        },false);
    }
    private void discard() {
        if(capture!=null) { capture.cancel(); capture=null; }
        if(sample!=null) { Arrays.fill(sample,(byte)0); sample=null; }
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }
    private JSONObject currentConfig() {
        JSONObject values=new JSONObject();
        try { values.put("app_id",appIdInput.getText().toString()).put("api_key",keyInput.getText().toString()).put("api_secret",secretInput.getText().toString()); }
        catch(JSONException ignored) {} return values;
    }
    private void saveConfig() {
        try {
            VoiceprintClient.Credentials credentials=new VoiceprintClient.Credentials(appIdInput.getText().toString(),keyInput.getText().toString(),secretInput.getText().toString());
            hideKeyboard();
            job("正在保存设置…",()-> {
                vault.save("voice_settings",credentials.json());
                main.post(()-> { configuring=false; configDraft=null; appIdInput=null; });
                return "设置已保存，可检查连接或准备声纹库";
            },false);
        } catch(Exception e) { notice.setText(safeError(e)); }
    }
    @Override protected void navigateBack() {
        if(busy) { notice.setText("正在完成当前操作，请稍候再返回"); return; }
        hideKeyboard();
        if(configuring) {
            JSONObject values=currentConfig(); boolean changed=false;
            for(String key:new String[]{"app_id","api_key","api_secret"}) if(!values.optString(key).equals(settings.optString(key))) changed=true;
            Runnable leave=()-> { configuring=false; configDraft=null; appIdInput=null; message=""; draw(); };
            if(!changed) { leave.run(); return; }
            AlertDialog dialog=new AlertDialog.Builder(this).setTitle("保存修改？").setMessage("讯飞声纹设置有尚未保存的修改。")
                .setNegativeButton("继续编辑",null).setNeutralButton("不保存",(d,w)->leave.run())
                .setPositiveButton("保存并返回",(d,w)->saveConfig()).create();
            dialog.getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE); dialog.show();
        } else if(capture!=null || sample!=null) {
            new AlertDialog.Builder(this).setTitle(capture!=null?"取消这次录音？":"丢弃这段录音？")
                .setMessage("返回声纹管理后，这段尚未上传的录音会被清除。")
                .setNegativeButton("继续",null).setPositiveButton("丢弃并返回",(d,w)-> { discard(); message="录音已取消，未上传"; draw(); }).show();
        } else finish();
    }
    @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] grants) {
        super.onRequestPermissionsResult(request,permissions,grants);
        if(request==40) {
            message=grants.length>0 && grants[0]==PackageManager.PERMISSION_GRANTED?"麦克风已允许，点击录制开始":"未获得麦克风权限。可在系统设置中允许后返回重试"; draw();
        }
    }
    @Override protected void onResume() { super.onResume(); foreground=true; }
    @Override protected void onPause() {
        foreground=false;
        if(capture!=null || sample!=null) { discard(); message="离开页面，未上传的录音已取消"; draw(); }
        super.onPause();
    }
    @Override protected void onDestroy() { discard(); super.onDestroy(); }
    private void open(String url) { try { startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url))); } catch(Exception e) { notice.setText("手机没有可打开链接的应用"); } }
}
