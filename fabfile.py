from __future__ import absolute_import
import os
import re
import json
from datetime import datetime, timedelta
import hashlib
import time

from fabric.api import env, hide, settings, cd, lcd
from fabric.operations import local as lrun, run, sudo, put
from fabric.contrib.console import confirm
from fabric.tasks import execute
from fabric.context_managers import shell_env, prefix
from fabric.contrib.files import exists
from ilogue.fexpect import expect, expecting, run as erun
import pexpect
from fabric.api import task as _task

remote_requirements = os.path.abspath('requirements/base.txt')

def console(message):
    print '\n[+] %s\n' % message

def task(function):
    name = function.__name__
    def wrapped(*args,**kwargs):
        console('executing %s...' % name)
        return function(*args,**kwargs)

    wrapped.__name__ = name
    return _task(wrapped)

def randhash():
    hash_algorithm = hashlib.sha1()
    seed = str(datetime.now())
    hash_algorithm.update(seed)
    return  hash_algorithm.hexdigest()

def render_to_file(template, destination_file, template_context={}):
    from django.template import Template, Context
    from django.conf import settings
    settings.configure()

    with open(template,'rb') as f:
        content = f.read()

    template_obj = Template(content)
    context = Context(template_context)
    rendered_content = template_obj.render(context)

    with open(destination_file,'wb') as f:
        f.write(rendered_content)

    return destination_file

def parse_config(config_file):
    with open(config_file,'rb') as f:
        config = json.load(f)
    return config

def write_config(config_file, config_dict):
    config_str = json.dumps(config_dict, indent=4, sort_keys=True)

    with open(config_file, 'wb') as f:
        f.write(config_str)

    return parse_config(config_file)

config = parse_config('config/ndim.conf')

@task
def local():
    env.run = erun
    env.hosts = ['localhost']
    env.local=True
    env.LOCAL_DEPLOY=True
    env.virtualenv = 'local'

@task
def remote_setup():
    env.local = False
    env.run = run
    env.key_filename = config.get('credentials').get('ssh_key_path')
    env.user = config.get('user')
    env.virtualenv = 'production'

    # make SURE python is 2.6; otherwise yum is dead.
    sudo('rm /usr/bin/python; ln -s /usr/bin/python2.6 /usr/bin/python;')

    # get updates
    sudo('yum update -y')

    # build tools
    sudo('yum install make automake gcc gcc-c++ kernel-devel git-core mysql mysql-server mysql-devel postgresql-devel patch ncurses ncurses-devel nginx -y')

    # rabbitmq
    sudo('yum install rabbitmq-server --enablerepo=epel -y')

    # make sure python 2.7 is installed
    sudo('yum install python27-devel -y')

    python_27_dir = sudo('python27 -c "from distutils.sysconfig import get_python_lib;'\
        'print(get_python_lib())"')

    with cd(python_27_dir):
        sudo('wget https://bitbucket.org/pypa/setuptools/raw/bootstrap/ez_setup.py -O - | sudo python')
        sudo('easy_install pip')

    sudo('pip install virtualenv')
    sudo('pip install virtualenvwrapper')

    # install pip remote requirements (obtained from requirements/remote.txt)
    with cd('/tmp'):
        with prefix('source $(which virtualenvwrapper.sh) && workon remote || mkvirtualenv %s' % env.virtualenv):
            put(remote_requirements,'requirements.txt')
            run('pip install -r requirements.txt')

    sudo('/etc/init.d/mysqld stop; /etc/init.d/mysqld start')
    sudo('rabbitmqctl stop; rabbitmq-server -detached',user='rabbitmq')

@task
def dbinit():
    """
    Creates a default database and user with appropriate privileges
    for local development
    """

    mysql_cmd = "create database {0};"+\
    "create user {1}@\"{2}\" identified by \"{3}\";"+\
    "create user {1}@localhost identified by \"{3}\";"+\
    "grant all privileges on {0}.* to {1}@localhost;"+\
    "grant all privileges on {0}.* to {1}@\"{2}\";exit;"

    mysql_prompts = []
    mysql_prompts += expect('mysql>',mysql_cmd.format(
        config.get('env_vars').get("DATABASE_NAME"),
        config.get('env_vars').get("DATABASE_USER"),
        config.get('env_vars').get("DATABASE_CONSUMER",'%'),
        config.get('env_vars').get("DATABASE_PASSWORD")))

    with expecting(mysql_prompts):
        erun('mysql -u root')

def retry(callback, retry_number):
    """
    Calls a function until it returns True, or runs out of attempts

    Paramters:
    ==========

        callback (function) - a function that returns a boolean success status

        retry_number (integer) - the number of retry attempts

    """
    retries = 0
    while retries < retry_number:
        if callback():
            return
        retries += 1
        time.sleep(1)
    raise Exception('Tried %s times to execute' % retries)

@task
def deploy():
    with prefix('source $(which virtualenvwrapper.sh) && workon %s || mkvirtualenv remote && pip install fexpect' % env.virtualenv):
        env_vars = config.get('env_vars')
        if env.local:
            env_vars.update({"LOCAL_DEPLOY":"True"})
        if not exists('~/projects'):
            run('mkdir ~/projects')
        if not exists('~/projects/ndim_project'):
                run('git clone https://github.com/jsalva/ndim ~/projects/ndim_project')
        with cd('~/projects/ndim_project/ndim'):
            if not exists('logs'):
                run('mkdir logs')
            run('git pull origin master')
            with shell_env(**env_vars):
                run('python manage.py collectstatic --noinput')
                run('python manage.py migrate')
                run('python manage.py syncdb --noinput')
                run('python manage.py supervisor shutdown')
                def supervisord():
                    with settings(warn_only=True):
                        result = run('python manage.py supervisor --daemonize')
                    return not result.failed
                retry(supervisord, 10)

    if not exists('/tmp/nginx'):
        run('mkdir /tmp/nginx')

    AWS_DNS = env.host_string
    hash_val = randhash()[:4]
    conf_template = os.path.abspath('config/nginx/nginx_ndim.conf')
    path, file_name = os.path.split(conf_template)
    name, ext = file_name.split('.')

    conf_rendered = '/tmp/{0}{1}.{2}'.format(name, hash_val, ext)
    render_to_file(conf_template, conf_rendered, {'AWS_DNS':AWS_DNS})

    if not env.local:
        put('nginx.conf','/etc/nginx/nginx.conf',use_sudo=True)
        put(conf_rendered,'/etc/nginx/conf.d/nginx_ndim.conf',use_sudo=True)
        put('ssl/ndim.key','/etc/ssl/certs/ndim.key',use_sudo=True)
        put('ssl/ndim.crt','/etc/ssl/certs/ndim.crt',use_sudo=True)
        put('nginx_ndim.conf','/etc/nginx/conf.d/nginx_ndim.conf',use_sudo=True)
        sudo('service nginx stop; service nginx start;')

@task
def aws():
    env.hosts = config.get('aws_nodes')
    execute(remote_setup)

