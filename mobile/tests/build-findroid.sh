set -euo pipefail
PIN=9a6264e1f0380529cebe01a9ae0a2c9bdb943954
mkdir -p companion
curl -fsSL "https://api.github.com/repos/jarnedemeulemeester/findroid/tarball/$PIN" | tar xz -C companion --strip-components=1
python3 - <<'PY'
from pathlib import Path
p=Path('companion/app/phone/build.gradle.kts');s=p.read_text().replace('applicationId = "dev.jdtech.jellyfin"','applicationId = "dev.jdtech.jellyfin.dubs"');p.write_text(s)
p=Path('companion/app/phone/src/main/res/values/strings.xml')
if p.exists():p.write_text(p.read_text().replace('>Findroid<','>Findroid Dubs<'))
PY
cd companion
./gradlew :app:phone:assembleLibreRelease --no-daemon
cd ..
mkdir -p artifacts
APK=$(find companion/app/phone/build/outputs/apk -iname '*arm64*v8a*release*.apk' | head -1)
if [ -z "$APK" ]; then APK=$(find companion/app/phone/build/outputs/apk -iname '*.apk' | head -1); fi
"$ANDROID_HOME/build-tools/36.0.0/zipalign" -P 16 -f 4 "$APK" artifacts/findroid-aligned.apk
"$ANDROID_HOME/build-tools/36.0.0/apksigner" sign --ks /tmp/dubs-signing.p12 --ks-type PKCS12 --ks-key-alias dubs --ks-pass env:DUBS_SIGNING_PASSWORD --key-pass env:DUBS_SIGNING_PASSWORD --out artifacts/Findroid-Dubs-1.0.0-arm64.apk artifacts/findroid-aligned.apk
"$ANDROID_HOME/build-tools/36.0.0/apksigner" verify artifacts/Findroid-Dubs-1.0.0-arm64.apk
python3 - <<'PY'
import zipfile,struct
from pathlib import Path
p=Path('artifacts/Findroid-Dubs-1.0.0-arm64.apk');z=zipfile.ZipFile(p)
for name in z.namelist():
 if not name.startswith('lib/arm64-v8a/') or not name.endswith('.so'):continue
 b=z.read(name);assert b[:5]==b'\x7fELF\x02';off=struct.unpack_from('<Q',b,32)[0];size,count=struct.unpack_from('<HH',b,54)
 for i in range(count):
  at=off+i*size;kind=struct.unpack_from('<I',b,at)[0]
  if kind==1:assert struct.unpack_from('<Q',b,at+48)[0]>=16384, name+' lacks16KB alignment'
print('Native ARM64 libraries16KB aligned')
PY
printf '%s\n' "$PIN" > artifacts/findroid-source-sha.txt
(cd companion && git diff --no-index /dev/null /dev/null || true)
