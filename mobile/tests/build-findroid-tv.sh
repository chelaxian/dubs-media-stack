set -euo pipefail
PIN=9a6264e1f0380529cebe01a9ae0a2c9bdb943954
mkdir -p companion
curl -fsSL "https://api.github.com/repos/jarnedemeulemeester/findroid/tarball/$PIN" | tar xz -C companion --strip-components=1
python3 tests/patch-findroid-tv.py
cd companion
./gradlew :app:tv:assembleLibreRelease --no-daemon
cd ..
mkdir -p artifacts
for ABI in arm64-v8a x86_64; do
 APK=$(find companion/app/tv/build/outputs/apk -iname "*$ABI*release*.apk" | head -1)
 test -n "$APK"
 "$ANDROID_HOME/build-tools/36.0.0/zipalign" -P 16 -f 4 "$APK" "artifacts/aligned-$ABI.apk"
 "$ANDROID_HOME/build-tools/36.0.0/apksigner" sign --ks /tmp/dubs-signing.p12 --ks-type PKCS12 --ks-key-alias dubs --ks-pass env:DUBS_SIGNING_PASSWORD --key-pass env:DUBS_SIGNING_PASSWORD --out "artifacts/Findroid-Dubs-TV-1.0.0-$ABI.apk" "artifacts/aligned-$ABI.apk"
 "$ANDROID_HOME/build-tools/36.0.0/apksigner" verify "artifacts/Findroid-Dubs-TV-1.0.0-$ABI.apk"
 "$ANDROID_HOME/build-tools/36.0.0/zipalign" -c -P 16 4 "artifacts/Findroid-Dubs-TV-1.0.0-$ABI.apk"
done
