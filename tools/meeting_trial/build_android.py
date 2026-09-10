"""Build APK, unit tests and lint with the checked-in Gradle wrapper.

Requires JDK 17 (JAVA_HOME) and Android SDK (ANDROID_HOME or local.properties).
"""
import os
from pathlib import Path
import subprocess
import sys

def main():
    root = Path(__file__).resolve().parents[2]
    android = root / 'companion/android'
    env = os.environ.copy()
    temporary = root / 'build/java-tmp'
    temporary.mkdir(parents=True, exist_ok=True)
    env['TEMP'] = env['TMP'] = str(temporary)
    # Keep Windows Java local sockets outside user directories containing spaces.
    jvm_options = ' '.join([
        '-Xmx2048m', '-Dfile.encoding=UTF-8', '-Djava.net.preferIPv4Stack=true',
        f'-Djava.io.tmpdir="{temporary.as_posix()}"',
        f'-Djdk.net.unixdomain.tmpdir="{temporary.as_posix()}"',
    ])
    wrapper = android / ('gradlew.bat' if os.name == 'nt' else 'gradlew')
    if not wrapper.exists():
        sys.exit('Missing Gradle wrapper; clone the complete repository.')
    command = [str(wrapper), '--no-daemon', '--stacktrace',
               '-Dorg.gradle.jvmargs=' + jvm_options,
               ':app:assembleDebug', ':app:testDebugUnitTest', ':app:lintDebug',
               *sys.argv[1:]]
    log = root / 'build/meeting-android-build.log'
    with log.open('w', encoding='utf-8') as output:
        result = subprocess.run(command, cwd=android, env=env,
                                stdout=output, stderr=subprocess.STDOUT)
    print('Android build exit:', result.returncode)
    print('Log:', log)
    if result.returncode:
        print(log.read_text(encoding='utf-8', errors='replace')[-5000:])
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
