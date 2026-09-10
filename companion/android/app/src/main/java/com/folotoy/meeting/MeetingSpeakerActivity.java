package com.folotoy.meeting;

import android.app.AlertDialog;
import android.content.Intent;
import android.os.*;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.util.*;

/** Read-only recognition until confirmation; the parent owns the meeting draft. */
public class MeetingSpeakerActivity extends BrandActivity {
    private final Handler main=new Handler(Looper.getMainLooper());
    private Vault vault; private MeetingDraft draft; private String id="",fingerprint="",account="",message="";
    private JSONArray report=new JSONArray(); private LinearLayout content; private TextView notice;
    private boolean busy; private MeetingAudio.Stop stop;
    private final Map<String,CheckBox> choices=new LinkedHashMap<>();
    @Override public void onCreate(Bundle state) {
        super.onCreate(state); vault=new Vault(this); id=getIntent().getStringExtra("task_id");
        try {
            if(id==null || !id.matches("[a-f0-9]{32}")) throw new IllegalArgumentException();
            draft=new MeetingDraft(vault.load("meeting_"+id)); fingerprint=SpeakerSamples.fingerprint(draft);
            JSONObject saved=vault.load("voice_report_"+id);
            account=Voiceprints.accountKey(vault.load("voice_settings").optString("app_id"));
            if(fingerprint.equals(saved.optString("fingerprint")) && account.equals(saved.optString("account"))) {
                JSONArray rows=saved.optJSONArray("rows"); if(rows!=null) report=rows;
                if(report.length()>0) message="显示上次识别候选；尚未应用的姓名需要你确认";
            }
        } catch(Exception e) { draft=null; message="无法读取会议资料，请返回后重新获取会议结果"; }
        draw();
    }
    private int dp(int n) { return BrandUi.dp(this,n); }
    private TextView text(String s,int size) {
        TextView v=new TextView(this); v.setText(s); v.setTextSize(size); v.setTextColor(BrandUi.INK);
        v.setPadding(0,dp(6),0,dp(10)); v.setLineSpacing(dp(3),1.05f); return v;
    }
    private void note(String s) { TextView v=text(s,14); v.setTextColor(BrandUi.MUTED); content.addView(v); }
    private void button(String s,Runnable action,boolean primary) {
        Button b=new Button(this); b.setText(s); BrandUi.button(this,b,primary);
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2); p.setMargins(0,dp(6),0,dp(6)); content.addView(b,p); b.setOnClickListener(v->action.run());
    }
    private void draw() {
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        if(busy) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON); else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        LinearLayout root=new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setBackgroundColor(BrandUi.BACK);
        root.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS); root.setPadding(dp(20),dp(18),dp(20),dp(12));
        root.setOnApplyWindowInsetsListener((v,insets)-> {
            if(Build.VERSION.SDK_INT>=30) { var bars=insets.getInsets(WindowInsets.Type.systemBars()); root.setPadding(dp(20)+bars.left,dp(18)+bars.top,dp(20)+bars.right,dp(12)+bars.bottom); }
            else root.setPadding(dp(20)+insets.getSystemWindowInsetLeft(),dp(18)+insets.getSystemWindowInsetTop(),dp(20)+insets.getSystemWindowInsetRight(),dp(12)+insets.getSystemWindowInsetBottom()); return insets;
        });
        setContentView(root); root.addView(BrandUi.back(this,"返回编辑纪要",this::navigateBack)); root.addView(text("识别本场发言人",28));
        notice=text(message,14); notice.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE); root.addView(notice);
        ScrollView scroll=new ScrollView(this); content=new LinearLayout(this); content.setOrientation(LinearLayout.VERTICAL); content.setPadding(0,dp(8),0,dp(24));
        scroll.addView(content); root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1)); choices.clear();
        if(draft==null) return;
        if(busy) {
            note("正在从听悟取样并查询讯飞。离开 App 会停止后续取样和请求；已经发出的请求仍可能计次。");
            button("取消识别",()-> { if(stop!=null) stop.cancel(); notice.setText("正在停止并清空音频内存…"); },false); return;
        }
        note("把听悟分出的发言人，与手机中已录入的讯飞声纹进行匹配。每位最多取两段 5 秒声音，一次最多处理前 8 位发言人、调用讯飞 16 次。");
        note("点击识别会从听悟读取本场录音，并将取出的片段上传到你的讯飞账号，可能消耗两家的试用或付费额度。音频只在手机内存中处理，完成或取消后清空。");
        note("需要在录音前开启「设置 → 录音偏好 → 会后声纹识别用音频」，保存并发送给AI通行证。旧会议没有音频时无法补做识别。");
        note("已用于补录声纹的片段会自动排除，只用未参与补录的声音验证。");
        button(report.length()==0?"取样并识别":"重新取样并识别",this::recognize,true);
        if(report.length()==0) return;
        content.addView(text("核对姓名候选",21)); note("请逐项勾选你确认的姓名。相似度不是成功率；已手动命名的发言人会保留原名。");
        JSONObject names=draft.data.optJSONObject("speakers");
        for(int i=0;i<report.length();i++) {
            JSONObject row=report.optJSONObject(i); if(row==null) continue;
            String speaker=row.optString("speaker"),name=row.optString("name"),old=names==null?"":names.optString(speaker);
            content.addView(text("发言人 "+speaker+(old.isBlank()?"":" · 已命名："+old),18));
            if(!name.isBlank()) {
                CheckBox choice=new CheckBox(this); choice.setText((row.optBoolean("tentative")?"低相似度候选 · ":"")+"确认为「"+name+"」"); choice.setTextColor(BrandUi.INK); choice.setTextSize(16); choice.setMinHeight(dp(52));
                choice.setButtonTintList(android.content.res.ColorStateList.valueOf(BrandUi.PRIMARY)); choice.setEnabled(old.isBlank()); content.addView(choice); choices.put(speaker,choice);
            }
            note(row.optString("reason")+(row.has("score")?String.format(Locale.CHINA," · 相似度 %.2f",row.optDouble("score")):""));
            button("用发言人 "+speaker+" 的声音补录声纹",()->supplementDialog(speaker),false);
        }
        note("确认后会更新本场逐字稿的发言人标签，以及纪要里明确写出的“发言人编号”。不会重新生成 AI 摘要；请校对结论与待办归属。已有飞书文档需在编辑页重新发布。");
        button("应用已勾选姓名",this::apply,true);
    }
    private void progress(String s) { main.post(()-> { if(!isDestroyed() && !isFinishing()) notice.setText(s); }); }
    private void recognize() {
        if(busy) return;
        busy=true; stop=new MeetingAudio.Stop(); MeetingAudio.Stop current=stop; message="正在检查会议音频和声纹库…"; draw();
        VoiceprintActivity.CLOUD.execute(()-> {
            JSONArray rows=new JSONArray(); String outcome; boolean persist=false;
            try {
                var credentials=VoiceprintClient.Credentials.from(vault.load("voice_settings")); String currentAccount=Voiceprints.accountKey(credentials.appId);
                Voiceprints service=new Voiceprints(new Voiceprints.Store() {
                    public JSONObject load(String key) throws Exception { return vault.load(key); }
                    public void save(String key,JSONObject value) throws Exception { vault.save(key,value); }
                },VoiceprintClient.live(credentials),credentials.appId);
                if(Voiceprints.readyCount(service.snapshot())==0) throw new IllegalStateException("请先到「声纹管理」录入至少一位发言人的声纹");
                var plan=SpeakerSamples.recognitionPlan(draft,service.snapshot()); if(plan.isEmpty()) throw new IllegalStateException("请先返回编辑页获取会议结果，再识别发言人");
                current.check(); String url=new Tingwu(vault.load("settings")).audio(id); current.check();
                try(MeetingAudio audio=new MeetingAudio(new MeetingAudio.Source(url,current),current)) {
                    int number=0;
                    for(var entry:plan.entrySet()) {
                        current.check(); number++; List<Voiceprints.Match> matches=new ArrayList<>();
                        for(var span:entry.getValue()) {
                            current.check(); progress("正在识别发言人 "+entry.getKey()+" · 第 "+(matches.size()+1)+" 段"); short[] pcm=null; byte[] wav=null;
                            try {
                                pcm=audio.sample(span);
                                if(!VoiceSample.qualityProblem(pcm).isEmpty()) { matches.add(new Voiceprints.Match("","片段过轻或失真，不能判断",Double.NaN)); continue; }
                                wav=VoiceSample.wav(pcm); current.check(); matches.add(service.identify(wav)); current.check();
                            } finally { if(pcm!=null) Arrays.fill(pcm,(short)0); if(wav!=null) Arrays.fill(wav,(byte)0); }
                        }
                        JSONObject row=SpeakerSamples.candidate(entry.getKey(),matches);
                        if(entry.getValue().isEmpty()) row.put("reason",number>SpeakerSamples.MAX_SPEAKERS?"本次最多处理前 8 位，请手动命名":"没有未用于补录、独立且连续的 5 秒发言；请手动命名或重新录一场"); rows.put(row);
                    }
                }
                current.check(); vault.save("voice_report_"+id,new JSONObject().put("fingerprint",fingerprint).put("account",currentAccount).put("rows",rows).put("created",System.currentTimeMillis()));
                account=currentAccount; persist=true; outcome="识别完成，音频内存已清空。请勾选并应用你确认的姓名";
            } catch(Exception e) { outcome=current.cancelled?"识别已取消，音频内存已清空；已发出的请求可能已计次":safeError(e); }
            String finalOutcome=outcome; boolean success=persist;
            main.post(()-> { busy=false; if(isDestroyed() || isFinishing()) return; if(success) report=rows; message=finalOutcome+(!success && report.length()>0?"。下方仍为上次保存的候选":""); draw(); });
        });
    }
    private String safeError(Exception e) {
        String s=e.getMessage();
        if(s==null || s.length()>160 || s.contains("http") || s.contains("\n") || !(e instanceof java.io.IOException || e instanceof IllegalStateException || e instanceof IllegalArgumentException)) return "识别未完成，请检查服务配置与网络后重试";
        try { JSONObject values=vault.load("voice_settings"); for(String key:new String[]{"api_key","api_secret","app_id"}) { String secret=values.optString(key); if(!secret.isEmpty()) s=s.replace(secret,"[已隐藏]"); } } catch(Exception ignored) {} return s;
    }
    private void apply() {
        try {
            String currentAccount=Voiceprints.accountKey(vault.load("voice_settings").optString("app_id"));
            if(!account.equals(currentAccount)) throw new IllegalStateException("声纹账号已变化，请重新识别");
            JSONObject people=vault.load(account).optJSONObject("people"),selected=new JSONObject();
            for(int i=0;i<report.length();i++) {
                JSONObject row=report.getJSONObject(i); String speaker=row.getString("speaker"); CheckBox choice=choices.get(speaker);
                if(choice==null || !choice.isEnabled() || !choice.isChecked()) continue;
                JSONObject person=people==null?null:people.optJSONObject(row.optString("feature_id"));
                if(person==null || !"ready".equals(person.optString("state")) || !row.optString("name").equals(person.optString("name"))) throw new IllegalStateException("候选声纹已变化，请重新识别");
                selected.put(speaker,row.getString("name"));
            }
            if(selected.length()==0) { notice.setText("请先勾选至少一位你确认的姓名"); return; }
            setResult(RESULT_OK,new Intent().putExtra("task_id",id).putExtra("fingerprint",fingerprint).putExtra("names",selected.toString())); finish();
        } catch(Exception e) { notice.setText(safeError(e)); }
    }
    private void supplementDialog(String speaker) {
        if(busy) return;
        try {
            var clips=SpeakerSamples.plan(draft).get(speaker);
            if(clips==null || clips.size()<2) throw new IllegalStateException("补录需要两段独立的 5 秒声音：一段补录，另一段验证。请重新录一场");
            var credentials=VoiceprintClient.Credentials.from(vault.load("voice_settings"));
            String targetAccount=Voiceprints.accountKey(credentials.appId); JSONObject state=vault.load(targetAccount),people=state.optJSONObject("people");
            List<String> ids=new ArrayList<>(),names=new ArrayList<>();
            if(people!=null) for(Iterator<String> it=people.keys();it.hasNext();) {
                String key=it.next(); JSONObject person=people.optJSONObject(key);
                if(person!=null && person.optString("state").equals("ready")) { ids.add(key); names.add(person.optString("name")); }
            }
            if(ids.isEmpty()) throw new IllegalStateException("请先录入并核对至少一位发言人的声纹");
            SpeakerSamples.Span span=clips.get(0);
            if(SpeakerSamples.usedForTraining(state,id,span)) throw new IllegalStateException("这场的首段声音已经补录过，请用未参与补录的片段识别或换一场新录音");
            new AlertDialog.Builder(this).setTitle("发言人 "+speaker+" 是哪位本人？").setItems(names.toArray(new String[0]),(d,which)->
                new AlertDialog.Builder(this).setTitle("确认本人声音并补录")
                    .setMessage(String.format(Locale.CHINA,"将本场发言人 %s 在 %.2f–%.2f 秒的声音，合并进「%s」的讯飞声纹。请确认是本人自愿提供的声音。\n\n保留原有样本，但不能单独撤销这次合并；需要时可删除声纹后重录。随后用未参与补录的片段再次识别。\n\n补录调用一次讯飞，验证最多再调用 16 次，可能消耗试用或付费额度。",speaker,span.start/1000.0,span.end/1000.0,names.get(which)))
                    .setNegativeButton("取消",null).setPositiveButton("本人确认，补录并验证",(dialog,w)->supplement(ids.get(which),targetAccount,span)).show()).show();
        } catch(Exception e) { notice.setText(safeError(e)); }
    }
    private void supplement(String feature,String targetAccount,SpeakerSamples.Span span) {
        if(busy) return;
        busy=true; stop=new MeetingAudio.Stop(); MeetingAudio.Stop current=stop; message="正在取样并补录本人声纹…"; draw();
        VoiceprintActivity.CLOUD.execute(()-> {
            boolean updated=false; String outcome; short[] pcm=null; byte[] wav=null;
            try {
                var credentials=VoiceprintClient.Credentials.from(vault.load("voice_settings"));
                if(!targetAccount.equals(Voiceprints.accountKey(credentials.appId))) throw new IllegalStateException("声纹账号已变化，请重新选择");
                Voiceprints service=new Voiceprints(new Voiceprints.Store() {
                    public JSONObject load(String key) throws Exception { return vault.load(key); }
                    public void save(String key,JSONObject value) throws Exception { vault.save(key,value); }
                },VoiceprintClient.live(credentials),credentials.appId);
                current.check(); String url=new Tingwu(vault.load("settings")).audio(id); current.check();
                try(MeetingAudio audio=new MeetingAudio(new MeetingAudio.Source(url,current),current)) { pcm=audio.sample(span); }
                if(!VoiceSample.qualityProblem(pcm).isEmpty()) throw new IllegalStateException("本场声音过轻或失真，不能用于补录");
                wav=VoiceSample.wav(pcm); current.check();
                vault.save("voice_report_"+id,new JSONObject());
                service.supplement(feature,wav,id,span); updated=true; outcome="讯飞已确认补录，正在用另一段声音独立验证";
            } catch(Exception e) { outcome=current.cancelled?"已停止后续操作；若补录请求已发出，请在声纹管理核对状态":safeError(e); }
            finally { if(pcm!=null) Arrays.fill(pcm,(short)0); if(wav!=null) Arrays.fill(wav,(byte)0); }
            boolean success=updated; String finalOutcome=outcome;
            main.post(()-> {
                busy=false; if(isDestroyed() || isFinishing()) return; report=new JSONArray(); message=finalOutcome; draw();
                if(success && !current.cancelled) recognize();
            });
        });
    }
    @Override protected void navigateBack() {
        if(busy) new AlertDialog.Builder(this).setTitle("停止识别并返回？").setMessage("将停止后续取样和上传。已发出的云端请求仍可能计次，会议姓名尚未修改。")
            .setNegativeButton("继续识别",null).setPositiveButton("停止并返回",(d,w)-> { stop.cancel(); finish(); }).show(); else finish();
    }
    @Override protected void onStop() { if(busy && stop!=null) stop.cancel(); super.onStop(); }
    @Override protected void onDestroy() { if(stop!=null) stop.cancel(); super.onDestroy(); }
}
