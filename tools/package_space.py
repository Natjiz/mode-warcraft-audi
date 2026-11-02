"""Create a small, reviewable Hugging Face Space bundle without weights/secrets."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import tempfile
from zipfile import ZipFile,ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]


def build(output):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        target=Path(directory)
        for name in ('app.py','README.md','requirements.txt','packages.txt'):
            shutil.copyfile(ROOT/'space'/name,target/name)
        for folder in ('space_runtime','companion/warcraft_audi'):
            for source in (ROOT/folder).glob('*.py'):
                destination=target/folder/source.name;destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,destination)
        for relative in ('companion/__init__.py','companion/catalog.json','voices/models.json','tools/fetch_voice_models.py'):
            destination=target/relative;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/relative,destination)
        (target/'tools/__init__.py').write_text('',encoding='utf-8')
        for card_path in (ROOT/'voices/npcs').glob('*.json'):
            card=json.loads(card_path.read_text(encoding='utf-8'))
            reference=(ROOT/card['reference']).resolve()
            if not reference.is_relative_to(ROOT) or hashlib.sha256(reference.read_bytes()).hexdigest()!=card['reference_sha256']:
                raise ValueError('Invalid approved/candidate reference')
            for source in (card_path,reference):
                destination=target/source.relative_to(ROOT);destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,destination)
        with ZipFile(output,'w',ZIP_DEFLATED) as archive:
            for source in sorted(target.rglob('*')):
                if source.is_file():archive.write(source,source.relative_to(target).as_posix())
    print('Space bundle:',output,output.stat().st_size,'bytes')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'dist/WarcraftAudi-ZeroGPU-0.6.0.zip')
    build(parser.parse_args().output)
