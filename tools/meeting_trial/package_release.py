"""Package a clean source revision's existing APK and companion app firmware.

This does not build, sign, flash, upload or publish. Verify APK signing separately.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True, help='Version without the v prefix, e.g. 0.1.0')
    parser.add_argument('--apk-certificate-sha256', help='Certificate digest from a successful apksigner verify')
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', args.version):
        parser.error('Invalid version')
    if args.apk_certificate_sha256 and not re.fullmatch(r'[0-9a-fA-F]{64}', args.apk_certificate_sha256):
        parser.error('Certificate digest must be 64 hexadecimal characters without separators')
    root = Path(__file__).resolve().parents[2]
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root).strip():
        parser.error('Commit source changes before packaging')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    apk_dir = root / 'companion/android/app/build/outputs/apk/debug'
    metadata = json.loads((apk_dir / 'output-metadata.json').read_text(encoding='utf-8'))
    element = metadata['elements'][0]
    if element['versionName'] != args.version or metadata['applicationId'] != 'com.folotoy.meeting':
        parser.error('APK output metadata does not match requested app/version')
    apk = apk_dir / element['outputFile']
    firmware = root / 'build/meeting_companion/trae_card.bin'
    image = firmware.read_bytes()
    if not 80 <= len(image) <= 0x300000 or image[0] != 0xE9 or struct.unpack_from('<H', image, 12)[0] != 5:
        parser.error('Firmware is not an ESP32-C3 application within the expected partition size')
    if struct.unpack_from('<I', image, 32)[0] != 0xABCD5432 or image[48:80].split(b'\0')[0].decode() != args.version:
        parser.error('Firmware app descriptor version mismatch')
    output = root / 'releases' / ('v' + args.version)
    if output.exists():
        parser.error('Release output already exists; use a fresh version or review existing files')
    output.mkdir(parents=True)
    assets = []
    for source, suffix in [(apk, 'android.apk'), (firmware, 'esp32c3-app.bin')]:
        target = output / f'folo-meeting-v{args.version}-{suffix}'
        shutil.copy2(source, target)
        assets.append({'file': target.name, 'bytes': target.stat().st_size, 'sha256': sha256(target)})
    manifest = {
        'version': args.version, 'tag': 'v' + args.version, 'source_commit': commit,
        'repository': 'https://github.com/1ikeMcFlurry/folo-meeting',
        'packaged_at_utc': datetime.now(timezone.utc).isoformat(),
        'android': {'application_id': metadata['applicationId'], 'version_code': element['versionCode'],
                    'build_type': 'debug', 'certificate_sha256': args.apk_certificate_sha256},
        'firmware': {'chip': 'esp32c3', 'flash_size_mb': 8, 'app_address': '0x10000',
                     'app_partition_max_bytes': 0x300000, 'image_type': 'application-only'},
        'build_tools': {'esp_idf': '5.5.3', 'jdk': '17', 'gradle': '8.13', 'android_compile_sdk': 36},
        'assets': assets,
    }
    manifest_file = output / 'release-manifest.json'
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    checked = [output / asset['file'] for asset in assets] + [manifest_file]
    (output / 'SHA256SUMS.txt').write_text(''.join(f'{sha256(path)}  {path.name}\n' for path in checked), encoding='utf-8')
    print('Release prepared:', output)
    print('Source commit:', commit)
    for asset in assets:
        print(asset['file'], asset['bytes'], asset['sha256'])


if __name__ == '__main__':
    main()
