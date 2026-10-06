"""Container entrypoint: resolve PORT in Python, without relying on shell expansion."""
import os
import uvicorn


def prepare_volume_user():
    """Initialize the mounted ledger directory, then run the app as browseruser.

    Railway mounts persistent volumes as root. Only the dedicated ledger
    directory is assigned; existing ledger files and contents are not rewritten.
    """
    mount = os.getenv('RAILWAY_VOLUME_MOUNT_PATH')
    if not mount or os.geteuid() != 0:
        return
    import pwd
    from pathlib import Path
    user = pwd.getpwnam('browseruser')
    root = Path(mount).resolve()
    folder = Path(os.environ['QUALITY_BUDGET_DIR']).resolve()
    if folder == root or root not in folder.parents:
        raise RuntimeError('Budget directory must be inside the mounted volume')
    folder.mkdir(parents=True, exist_ok=True)
    os.chown(folder, user.pw_uid, user.pw_gid)
    os.setgroups([])
    os.setgid(user.pw_gid)
    os.setuid(user.pw_uid)


def main():
    prepare_volume_user()
    uvicorn.run('api_server:app', host='0.0.0.0', port=int(os.environ.get('PORT', '8000')))


if __name__ == '__main__':
    main()
