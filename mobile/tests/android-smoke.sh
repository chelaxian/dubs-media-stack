set -euo pipefail
adb install -r artifacts/Dubs-1.2.0.apk
adb shell am start -W -n app.dubs.panel/.MainActivity
sleep 15
adb shell pidof app.dubs.panel
adb shell uiautomator dump /sdcard/qa.xml
adb pull /sdcard/qa.xml artifacts/qa-android-ui.xml
adb exec-out screencap -p > artifacts/qa-android.png
adb logcat -d -t 1500 > artifacts/qa-android-log.txt
if grep -A 8 'FATAL EXCEPTION' artifacts/qa-android-log.txt | grep -q app.dubs.panel; then exit 1; fi
python3 - <<'PY'
from pathlib import Path
s=Path('artifacts/qa-android-ui.xml').read_text()
assert 'Адрес веб-панели' in s, 'Missing first-run server setup'
assert 'panel.example.org' not in s, 'Personal server must not be prefilled'
assert 'https://panel.example.org' in s, 'Missing neutral URL hint'
# WebView accessibility nodes are absent on some stock images; verify pixels with OCR instead.
PY

tesseract artifacts/qa-android.png artifacts/qa-android-ocr -l rus+eng 2>/dev/null
grep -Eqi "Адрес|веб-панели|Подключиться" artifacts/qa-android-ocr.txt
