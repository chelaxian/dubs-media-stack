#!/usr/bin/env python3
"""Package an immutable Git commit; never copy live configs or working files."""
import argparse
import gzip
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
TREES = ('src/', 'installer/', 'automations/', 'templates/', 'compose/', 'docs/', 'mobile/', 'tools/')
FILES = {'install.sh','update.sh','README.md','LICENSE','THIRD_PARTY.md','requirements.txt','studios.json'}


def allowed(name):
    p = Path(name)
    if not (name in FILES or name.startswith(TREES)):
        return False
    if any(part.startswith('.') and part != '.env.example' for part in p.parts):
        return False
    if any(part in ('build','dist','node_modules','__pycache__','Payload') for part in p.parts):
        return False
    if p.name.startswith('.env') and p.name != '.env.example':
        return False
    return p.suffix.lower() not in ('.key','.p12','.pfx','.pem','.ipa','.apk','.pyc','.log','.sqlite3')


def git(*args):
    return subprocess.check_output(['git','-C',str(ROOT),*args])


def package(ref, output):
    sha = git('rev-parse','--verify',ref+'^{commit}').decode().strip()
    epoch = int(git('show','-s','--format=%ct',sha))
    names = [n for n in git('ls-tree','-r','--name-only',sha).decode().splitlines() if allowed(n)]
    assert 'install.sh' in names and 'installer/wizard.py' in names, 'Release commit lacks wizard'
    raw = git('archive','--format=tar','--prefix=dubs-media-stack/',sha,'--',*names)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf,mode='w',format=tarfile.PAX_FORMAT) as target:
        with tarfile.open(fileobj=io.BytesIO(raw)) as source:
            for item in source:
                if not (item.isfile() or item.isdir()):
                    raise ValueError('Release must not contain links or special files: '+item.name)
                item.uid=item.gid=0;item.uname=item.gname='';item.mtime=epoch;item.pax_headers={}
                target.addfile(item,source.extractfile(item) if item.isfile() else None)
        value=(sha+'\n').encode();item=tarfile.TarInfo('dubs-media-stack/SOURCE_COMMIT.txt')
        item.size=len(value);item.mtime=epoch;item.mode=0o644;target.addfile(item,io.BytesIO(value))
    output.mkdir(parents=True,exist_ok=True)
    path=output/('dubs-media-stack-'+sha[:12]+'.tar.gz')
    with path.open('wb') as f:
        with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0) as z:z.write(buf.getvalue())
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(path.suffix+'.sha256').write_text(digest+'  '+path.name+'\n')
    print('Source commit:',sha)
    print('Archive:',path)
    print('SHA256:',digest)
    return path


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ref',default='HEAD',help='Immutable commit/ref to resolve and package')
    p.add_argument('--output',type=Path,default=ROOT/'dist')
    a=p.parse_args();package(a.ref,a.output)
