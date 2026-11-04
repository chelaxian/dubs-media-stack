import json,subprocess,time,os
from pathlib import Path
def run(*args):return subprocess.check_output(args,text=True).strip()
devices=json.loads(run('xcrun','simctl','list','devices','available','-j'))['devices'];phone=next(x for group in devices.values() for x in group if 'iPhone' in x['name']);uid=phone['udid']
subprocess.run(['xcrun','simctl','boot',uid],check=False);run('xcrun','simctl','bootstatus',uid,'-b');app='ios/build-simulator/Build/Products/Debug-iphonesimulator/Dubs.app';run('xcrun','simctl','install',uid,app);launched=run('xcrun','simctl','launch',uid,'app.dubs.panel');pid=launched.rsplit(':',1)[-1].strip();time.sleep(20);run('xcrun','simctl','io',uid,'screenshot','artifacts/qa-ios.png');
import shutil
for report in (Path.home()/'Library/Logs/DiagnosticReports').glob('Dubs*'):
 shutil.copy2(report,Path('artifacts')/('qa-'+report.name))
subprocess.run(['xcrun','simctl','spawn',uid,'log','show','--last','2m','--predicate','process == \"Dubs\"','--style','compact'],stdout=open('artifacts/qa-ios-log.txt','w'),stderr=subprocess.STDOUT)
run('kill','-0',pid);Path('artifacts/qa-ios-launch.txt').write_text(launched+'\n'+phone['name']+'\n');print('iOS simulator process alive',phone['name'])

# Reinstall on a clean data container for first-run UI checks.
run('xcrun','simctl','uninstall',uid,'app.dubs.panel')
subprocess.run(['xcodebuild','-project','ios/Dubs.xcodeproj','-scheme','Dubs','-configuration','Debug','-destination','platform=iOS Simulator,id='+uid,'-derivedDataPath','ios/build-ui-tests','CODE_SIGNING_ALLOWED=NO','test'],check=True,stdout=open('artifacts/qa-ios-uitests.log','w'),stderr=subprocess.STDOUT)
