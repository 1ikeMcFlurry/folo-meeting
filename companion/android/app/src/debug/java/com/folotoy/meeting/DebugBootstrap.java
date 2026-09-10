package com.folotoy.meeting;

import android.content.Context;
import org.json.JSONObject;
import java.io.File;
import java.nio.file.Files;
import java.util.Arrays;

/** USB-debug-only test setup. No exported component accepts credentials. */
public final class DebugBootstrap {
    public static void run(Context context) throws Exception {
        // Test builds stay awake only while this Activity is visible. This
        // changes no phone setting and is absent from release builds.
        if(context instanceof android.app.Activity activity)
            activity.getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        File file=new File(context.getFilesDir(),"bootstrap.json");
        if(!file.exists()) return;
        byte[] bytes=Files.readAllBytes(file.toPath());
        try {
            Vault vault=new Vault(context); JSONObject merged=vault.load("settings");
            JSONObject incoming=new JSONObject(new String(bytes,java.nio.charset.StandardCharsets.UTF_8));
            for(java.util.Iterator<String> it=incoming.keys();it.hasNext();) { String key=it.next(); merged.put(key,incoming.get(key)); }
            vault.save("settings",merged);
        }
        finally { Arrays.fill(bytes,(byte)0); Files.deleteIfExists(file.toPath()); }
    }
}
