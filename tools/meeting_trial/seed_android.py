"""Provision the connected debug App from the already-authorized trial account.

Credentials never enter the APK, shell arguments, logs or shared phone storage.
The debug-only app-private bootstrap is consumed into Android Keystore storage.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from device_trial import load_config

sys.stdout.reconfigure(encoding="utf-8")
ADB="D:/Software/Android/Sdk/platform-tools/adb.exe"
PACKAGE="com.folotoy.meeting"

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect-feishu-schema",action="store_true")
    parser.add_argument("--with-feishu-app",action="store_true",help="Reuse the configured CLI application, with a new phone OAuth authorization")
    args=parser.parse_args()
    path=Path.home()/".lark-cli/config.json"
    if args.inspect_feishu_schema:
        if not path.exists(): print("Feishu config not found"); return
        def schema(obj,depth=0):
            if depth>3: return type(obj).__name__
            if isinstance(obj,dict): return {key:schema(value,depth+1) for key,value in obj.items()}
            if isinstance(obj,list): return [schema(obj[0],depth+1)] if obj else []
            return type(obj).__name__
        conf=json.loads(path.read_text(encoding="utf-8"))
        print(json.dumps(schema(conf),ensure_ascii=False))
        print("Secret storage:", [app.get("appSecret",{}).get("source") for app in conf.get("apps",[])])
        return
    load_config(Path(__file__).with_name(".env"))
    data={"ak_id":os.environ["ALIBABA_CLOUD_ACCESS_KEY_ID"],"ak_secret":os.environ["ALIBABA_CLOUD_ACCESS_KEY_SECRET"],
          "app_key":os.environ["TINGWU_APP_KEY"],"max_minutes":"1","export_mode":"phone"}
    if path.exists():
        config=json.loads(path.read_text(encoding="utf-8"))
        # Only plain user-owned application credentials are eligible. Never copy
        # a PC user token or share its rotating refresh-token chain with mobile.
        profiles=config.get("profiles",{})
        profile=profiles.get(config.get("defaultProfile",""),{}) if isinstance(profiles,dict) else {}
        if not profile and len(config.get("apps",[]))==1: profile=config["apps"][0]
        if isinstance(profile,dict):
            for source,target in (("appId","feishu_app_id"),("appSecret","feishu_app_secret")):
                if isinstance(profile.get(source),str): data[target]=profile[source]
            if args.with_feishu_app:
                from windows_feishu_app import app_secret
                data["feishu_app_secret"]=app_secret(profile)
    payload=json.dumps(data,ensure_ascii=False).encode("utf-8")
    subprocess.run([ADB,"shell","am","force-stop",PACKAGE],check=True,capture_output=True)
    # Raw shell-v2 stdin, without a PTY, redirection, or a nested shell. dd
    # writes only the private target and never echoes its input.
    subprocess.run([ADB,"shell","run-as",PACKAGE,"mkdir","-p","files"],check=True,capture_output=True)
    command=[ADB,"shell","-T","run-as",PACKAGE,"dd","of=files/bootstrap.json","status=none"]
    result=subprocess.run(command,input=payload,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if result.returncode: raise RuntimeError("App-private setup failed; no credential output retained")
    size=subprocess.check_output([ADB,"shell","run-as",PACKAGE,"wc","-c","files/bootstrap.json"]).split()[0]
    if int(size)!=len(payload):
        subprocess.run([ADB,"shell","run-as",PACKAGE,"rm","-f","files/bootstrap.json"],capture_output=True)
        raise RuntimeError("Private USB transfer size did not match; setup was not applied")
    try:
        subprocess.run([ADB,"shell","am","start","-n",PACKAGE+"/.MainActivity"],check=True,capture_output=True)
        print("App setup sent; private bootstrap is consumed on launch.")
    finally:
        # The Activity consumes it synchronously in onCreate. A later verifier
        # checks its removal instead of deleting a file before the Activity reads.
        payload=b""

if __name__=="__main__": main()
