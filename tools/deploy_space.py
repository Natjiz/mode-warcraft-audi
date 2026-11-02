"""Deploy the reviewed bundle using configured HF SDK credentials; free hardware only."""
from pathlib import Path
import argparse
import json
import hashlib
import os
import re
import tempfile
from zipfile import ZipFile


def bundle_revision(bundle):
    with ZipFile(bundle) as archive:
        digest=hashlib.sha256(b'warcraft-audi-generation-v3')
        for name in ('voices/models.json','companion/warcraft_audi/voice_engine.py',
                     'companion/warcraft_audi/voice_profiles.py','companion/warcraft_audi/voice_quality.py'):
            digest.update(archive.read(name))
        for name in sorted(n for n in archive.namelist() if n.startswith('voices/npcs/') and n.endswith('.json')):
            card=json.loads(archive.read(name))
            digest.update(json.dumps({k:card[k] for k in ('npc_id','voice_version','reference_sha256')},sort_keys=True).encode())
        return digest.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner',required=True)
    parser.add_argument('--name',default='warcraft-audi')
    parser.add_argument('--library',default='warcraft-audi-library')
    parser.add_argument('--bundle',type=Path,default=Path('dist/WarcraftAudi-ZeroGPU-0.6.0.zip'))
    args=parser.parse_args()
    for name in (args.owner,args.name,args.library):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+",name):raise ValueError("Invalid repository name")
    if not args.bundle.is_file():raise ValueError("Build the reviewed Space bundle first")
    generation_revision=bundle_revision(args.bundle)
    from huggingface_hub import HfApi,get_token,SpaceHardware
    api=HfApi();identity=api.whoami()
    allowed={identity['name'],*(org['name'] for org in identity.get('orgs',[]))}
    if args.owner not in allowed:raise ValueError('Configured account cannot deploy under this owner')
    token=get_token()
    if not token:raise ValueError('Configure HF_TOKEN securely before deployment')
    space=f'{args.owner}/{args.name}';library=f'{args.owner}/{args.library}'
    # No paid fallback or credit purchase. The platform rejects ineligible accounts.
    api.create_repo(space,repo_type='space',space_sdk='gradio',space_hardware=SpaceHardware.ZERO_A10G,private=False,exist_ok=True)
    api.request_space_hardware(space,hardware=SpaceHardware.ZERO_A10G)
    api.create_repo(library,repo_type='dataset',private=False,exist_ok=True)
    api.add_space_variable(space,key='WCA_LIBRARY_REPO',value=library)
    api.add_space_secret(space,key='HF_TOKEN',value=token)
    with tempfile.TemporaryDirectory() as directory:
        with ZipFile(args.bundle) as archive:
            for name in archive.namelist():
                path=Path(name)
                if path.is_absolute() or '..' in path.parts:raise ValueError('Unsafe Space bundle')
            archive.extractall(directory)
        api.upload_folder(repo_id=space,repo_type='space',folder_path=directory,commit_message='Deploy Warcraft Audi ZeroGPU adapter')
    info=api.space_info(space)
    host=getattr(info,'host',None)
    if not host:
        print('Space created; obtain its hf.space host after the build:',space)
        return
    # An output proposal, not automatic activation before testing the running Space.
    proposal={'schema_version':1,'provider':'gradio-zerogpu','space_url':host if host.startswith('https://') else 'https://'+host,'generation_revision':generation_revision}
    Path('dist').mkdir(exist_ok=True)
    Path('dist/service-proposal.json').write_text(json.dumps(proposal,indent=2)+'\n',encoding='utf-8')
    print('Free Space deployed:',space,'; endpoint proposal saved to dist/service-proposal.json')


if __name__=='__main__':main()
