from distutils.util import strtobool
import re
from fabric.api import env, local, abort, sudo, cd, run, task
from fabric.colors import green, red, blue, cyan
from fabric.context_managers import prefix
from fabric.decorators import hosts, with_settings


## Example settings
# USER = "intranet"
# GROUP = "intranet"
# HOST = 'ticketing.protectamerica.com'
#
# PROD_SETTINGS = deploy.BASE_SETTINGS(
#     HOST=HOST,
#     DEPLOY_PATH='/deploy/intranet',
#     USER=USER,
#     GROUP=GROUP,
#     BOUNCE_SERVICES=[
#         'intranet',
#         'intranet_celery',
#         'intranet_celerybeat',
#         'intranet_mail',
#         'intranet_snet',
#         'intranet_custidx'
#     ],
#     EXTRA_COMMANDS=[
#         'sudo cp crons/-etc-cron.d-restart_intranet_mail /etc/cron.d/restart_intranet_mail',
#         'sudo chown root:root /etc/cron.d/restart_intranet_mail',
#         # 'touch collected-assets/less/style.less',
#     ]
# )
#
# env.host_string = PROD_SETTINGS.HOST
# env.deploy_settings = PROD_SETTINGS
##


DEFAULT_SETTINGS = dict(
    REQUIRE_CLEAN=True,
    SKIP_SYNCDB=False,
    SKIP_MIGRATE=False,
    BRANCH_NAME='master',
    DJANGO_PROJECT=True,
)


class BASE_SETTINGS(object):
    def __init__(self, *args, **kwargs):
        self.__dict__.update(DEFAULT_SETTINGS)
        if "USER" in kwargs:
            self.CRONTAB_OWNER = kwargs['USER']
        if "USER" in kwargs and "GROUP" in kwargs:
            self.CHOWN_TARGET = kwargs['USER'] + ':' + kwargs['GROUP']
        if "GIT_TREE" not in kwargs:
            self.GIT_TREE = kwargs['DEPLOY_PATH']
        # Overide any of these automatically set settings from kwargs
        self.__dict__.update(kwargs)


def bool_opt(opt, kwargs, default=False):
    """
    Will convert opt strings to python True/False, if it exists in kwargs.
    Or, will return what is in the deploy_settings if it exists there.
    Finally, will return the default if it doesn't exist in either.
    """

    opt = opt.lower()
    default = kwargs[opt] if opt in kwargs else getattr(env.deploy_settings, opt.upper(), default)
    if type(default) == str:
        return strtobool(default)
    return default


def django_check():
    if not getattr(env.deploy_settings, 'DJANGO_PROJECT', False):
        print red("This deployment is not configured as a DJANGO_PROJECT")
        return False
    return True

@task
def is_local_clean(*args, **kwargs):
    print cyan("Ensuring local working area is clean...")
    has_changes = local("git status --porcelain", capture=True)
    if has_changes:
        abort(red("Your working directory is not clean."))

    return not has_changes

@task
def is_remote_clean(*args, **kwargs):
    print cyan("Ensuring remote working area is clean...")
    git_cmd = "git --work-tree={0} --git-dir={0}/.git".format(env.deploy_settings.DEPLOY_PATH)
    has_changes = run(git_cmd + " status --porcelain")
    if has_changes:
        abort(red("Remote working directory is not clean."))

    return not has_changes

@task
def fix_project_owners(*args, **kwargs):
    with cd(env.deploy_settings.DEPLOY_PATH):
        print cyan('Fixing project owners')
        sudo('chown %s -R *' % env.deploy_settings.CHOWN_TARGET)
        sudo('chown %s -R .git*' % env.deploy_settings.CHOWN_TARGET)
        sudo('if [ -e .env ]; then chown %s -R .env; fi' % env.deploy_settings.CHOWN_TARGET)
        sudo('if [ -e env ]; then chown %s -R env; fi' % env.deploy_settings.CHOWN_TARGET)
        print ""

@task
def pull(*args, **kwargs):
    """:branch= sets the desired branch"""

    default_branch = getattr(env.deploy_settings, 'BRANCH_NAME', 'master')
    branch = kwargs.get('branch', default_branch)
    print cyan("Pulling from {0}".format(branch))
    with cd(env.deploy_settings.DEPLOY_PATH):
        run('git fetch')
        run('git checkout {0}'.format(branch))
        run('git pull')


@task
def update_submodules(*args, **kwargs):
    with cd(env.deploy_settings.DEPLOY_PATH):
            print cyan('Initializing submodules')
            run('git submodule init')
            print ""

            print cyan('Updating submodules')
            run('git submodule update')
            print ""

@task
def fix_logfile_permissions(*args, **kwargs):
    with cd(env.deploy_settings.DEPLOY_PATH):
        if getattr(env.deploy_settings, 'LOGS_PATH', False):
            print cyan("Ensuring proper permissions on log files (-rw-rw-r--)")
            sudo("chmod --preserve-root --changes a+r,ug+w -R %s" % env.deploy_settings.LOGS_PATH)
            print ""

@task
def install_requirements(*args, **kwargs):
    with cd(env.deploy_settings.DEPLOY_PATH):
        with prefix("source activate"):
            print cyan("Installing from requirements.txt")
            run("pip install -r requirements.txt")

@task
def collect_static(*args, **kwargs):
    print cyan("Collecting static resources")
    if not django_check():
        return
    with cd(env.deploy_settings.DEPLOY_PATH):
        with prefix('source activate'):
            # Setting verbose to minimal outupt
            # We aren't going to prompt if we really want to collectstatic
            run("./manage.py collectstatic -v0 --noinput")

@task
def run_migrations(*args, **kwargs):
    print cyan("Running migrations")
    if not django_check():
        return
    with cd(env.deploy_settings.DEPLOY_PATH):
        with prefix('source activate'):
            run("./manage.py migrate")

@task
def run_extras(*args, **kwargs):
    with cd(env.deploy_settings.DEPLOY_PATH):
        with prefix('source activate'):
            for cmd in getattr(env.deploy_settings, 'EXTRA_COMMANDS', []):
                print cyan('Extra:  ' + cmd)
                run(cmd)

@task
def restart_nginx(*args, **kwargs):
    print cyan("Restarting Nginx")
    sudo('service nginx restart')

@task
def bounce_services(*args, **kwargs):
    """:restart_nginx=True will also restart nginx"""

    print cyan("Bouncing processes...")
    for service in env.deploy_settings.BOUNCE_SERVICES:
        if bool_opt("bounce_services_only_if_running", kwargs, default=False):
            status = sudo('service %s status' % service, quiet=True)
            if re.search(r'{} stop/waiting'.format(service), status):
                print red("{} NOT bouncing.".format(status))
                continue
        sudo('service %s restart' % service)

    if bool_opt('restart_nginx', kwargs, default=False):
        restart_nginx()


@task
def services_status(*args, **kwargs):
    for service in env.deploy_settings.BOUNCE_SERVICES:
        status = sudo('service %s status' % service, quiet=True)
        hilight = green
        if re.search(r'{} stop/waiting'.format(service), status):
            hilight = red
        print hilight(status)

@task
def update_crontab(*args, **kwargs):
    if getattr(env.deploy_settings, 'CRON_FILE', None) and \
       getattr(env.deploy_settings, 'CRONTAB_OWNER', None):
        print green("Updating crontab...")
        sudo('crontab -u %s %s' % (env.deploy_settings.CRONTAB_OWNER,
                                   env.deploy_settings.CRON_FILE))
        print ""

@task
def sync_db(*args, **kwargs):
    print cyan("Sync DB")
    if not django_check():
        return
    with cd(env.deploy_settings.DEPLOY_PATH):
        with prefix('source activate'):
            if not getattr(env.deploy_settings, 'SKIP_SYNCDB', False):
                run("./manage.py syncdb")

@task(default=True)
def full_deploy(*args, **kwargs):
    """:require_clean=False will deploy even if local repo is not clean

    Requirements:
        - Must have a clean working directory
        - Remote must have a clean working directory

    Steps:
        - Change to project directory
        - Activating environment
        - Install all requirements
        - Run git fetch to pull down all changes
        - Updating submodules
        - Changing owner:group to draftboard
        - Bounce the webserver
    """

    print green("Beginning deployment...")
    print ""

    print blue('Checking pre-requisites...')

    if bool_opt('require_clean', kwargs, default=True):
        is_local_clean()

    is_remote_clean()

    print ""
    print green("Starting deployment...")
    print ""

    print green("Updating environment...")

    fix_project_owners()

    pull(**kwargs)

    update_submodules()

    fix_logfile_permissions()

    install_requirements()

    collect_static()

    if not bool_opt('skip_syncdb', kwargs, default=False):
        sync_db()

    if not bool_opt('skip_migrate', kwargs, default=False):
        run_migrations()

    run_extras()

    # post fix owners after checkout and other actions
    fix_project_owners()

    bounce_services()

    update_crontab()

    print green("Done!")


@task
def full_deploy_with_migrate(*args, **kwargs):
    # env.deploy_settings.SKIP_MIGRATE = False
    full_deploy(*args, skip_migrate=False, **kwargs)