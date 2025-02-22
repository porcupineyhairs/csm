# =============================================================================
# Copyright (c) 2016, Cisco Systems, Inc
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
# Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF
# THE POSSIBILITY OF SUCH DAMAGE.
# =============================================================================
from flask_login import current_user
from flask import g, send_file
from sqlalchemy import or_, and_, not_
from sqlalchemy import func

from csm_exceptions.exceptions import ValueNotFound
from csm_exceptions.exceptions import OperationNotAllowed

from constants import ServerType
from constants import UserPrivilege
from constants import InstallAction
from constants import PackageType
from constants import PackageState
from constants import JobStatus
from constants import get_user_privilege_list

from models import Server
from models import Host
from models import JumpHost
from models import Region
from models import User
from models import SMTPServer
from models import logger
from models import SoftwareProfile
from models import ConformanceReport

from models import Package
from models import InstallJob
from models import SMUMeta
from models import DownloadJob
from models import InstallJobHistory
from models import CustomCommandProfile
from models import get_download_job_key_dict
from models import InventoryJob
from models import HostContext
from models import ConnectionParam

from database import DBSession

from filters import get_datetime_string
from filters import time_difference_UTC

from utils import is_empty
from utils import get_datetime
from utils import remove_extra_spaces
from utils import create_directory
from utils import create_temp_user_directory
from utils import make_file_writable
from utils import check_acceptable_string

from smu_info_loader import SMUInfoLoader
from package_utils import is_file_acceptable_for_install_add

import os
import zipfile


def fill_servers(choices, servers, include_local=True):
    # Remove all the existing entries
    del choices[:]
    choices.append((-1, ''))
    
    if len(servers) > 0:
        for server in servers:
            if include_local or server.server_type != ServerType.LOCAL_SERVER:
                choices.append((server.id, server.hostname))
             
                    
def fill_dependencies(choices):
    # Remove all the existing entries
    del choices[:] 
    choices.append((-1, 'None'))  
     
    # The install action is listed in implicit ordering.  This ordering
    # is used to formulate the dependency.
    choices.append((InstallAction.PRE_UPGRADE, InstallAction.PRE_UPGRADE))
    choices.append((InstallAction.INSTALL_ADD, InstallAction.INSTALL_ADD))
    choices.append((InstallAction.INSTALL_ACTIVATE, InstallAction.INSTALL_ACTIVATE)) 
    choices.append((InstallAction.POST_UPGRADE, InstallAction.POST_UPGRADE))
    choices.append((InstallAction.INSTALL_COMMIT, InstallAction.INSTALL_COMMIT)) 


def fill_dependency_from_host_install_jobs(choices, install_jobs, current_install_job_id):
    # Remove all the existing entries
    del choices[:]
    choices.append((-1, 'None'))
    
    for install_job in install_jobs:
        if install_job.id != current_install_job_id:
            choices.append((install_job.id, '{} - {}'.format(install_job.install_action,
                            get_datetime_string(install_job.scheduled_time))))


def delete_install_job_dependencies(db_session, id):
    deleted = []
    dependencies = db_session.query(InstallJob).filter(InstallJob.dependency == id).all()
    for dependency in dependencies:
        if dependency.status in [JobStatus.SCHEDULED, JobStatus.FAILED]:
            db_session.delete(dependency)
            deleted.append(dependency.id)
        deleted = list(set((delete_install_job_dependencies(db_session, dependency.id)) + deleted))
    return deleted


def fill_jump_hosts(db_session, choices):
    # Remove all the existing entries
    del choices[:]
    choices.append((-1, ''))

    try:
        hosts = get_jump_host_list(db_session)
        if hosts is not None:
            for host in hosts:
                choices.append((host.id, host.hostname))
    except:
        logger.exception('fill_jump_hosts() hit exception')


def fill_regions(db_session, choices):
    # Remove all the existing entries
    del choices[:]
    choices.append((-1, ''))

    try:
        regions = get_region_list(db_session)
        if regions is not None:
            for region in regions:
                choices.append((region.id, region.name))
    except:
        logger.exception('fill_regions() hit exception')


def get_region_id_to_name_dict(db_session):
    results = dict()

    regions = get_region_list(db_session)
    for region in regions:
        results[region.id] = region.name

    return results


def get_region_name_to_id_dict(db_session):
    results = dict()

    regions = get_region_list(db_session)
    for region in regions:
        results[region.name] = region.id

    return results


def get_jump_host_id_to_name_dict(db_session):
    results = dict()

    jump_hosts = get_jump_host_list(db_session)
    for jump_host in jump_hosts:
        results[jump_host.id] = jump_host.hostname

    return results


def get_jump_host_name_to_id_dict(db_session):
    results = dict()

    jump_hosts = get_jump_host_list(db_session)
    for jump_host in jump_hosts:
        results[jump_host.hostname] = jump_host.id

    return results


def get_software_profile_id_to_name_dict(db_session):
    results = dict()

    software_profiles = get_software_profile_list(db_session)
    for software_profile in software_profiles:
        results[software_profile.id] = software_profile.name

    return results


def get_software_profile_name_to_id_dict(db_session):
    results = dict()

    software_profiles = get_software_profile_list(db_session)
    for software_profile in software_profiles:
        results[software_profile.name] = software_profile.id

    return results


def get_custom_command_profile_name_to_id_dict(db_session):
    results = dict()

    custom_command_profiles = get_custom_command_profile_list(db_session)
    for custom_command_profile in custom_command_profiles:
        results[custom_command_profile.profile_name] = custom_command_profile.id

    return results


def fill_user_privileges(choices):
    # Remove all the existing entries
    del choices[:]
    choices.append(('', ''))

    user_privileges = get_user_privilege_list()
    for user_privilege in user_privileges:
        choices.append((user_privilege, user_privilege))


def fill_software_profiles(db_session, choices):
    # Remove all the existing entries
    del choices[:]
    choices.append((-1, ''))

    try:
        software_profiles = get_software_profile_list(db_session)
        if software_profiles is not None:
            for software_profile in software_profiles:
                choices.append((software_profile.id, software_profile.name))
    except:
        logger.exception('fill_software_profiles() hit exception')


def fill_default_region(choices, region):
    # Remove all the existing entries
    del choices[:]

    try:
        if region is not None:
            choices.append((region.id, region.name))
    except:
        logger.exception('fill_default_region() hits exception')


def fill_custom_command_profiles(db_session, choices):
    del choices[:]

    try:
        profiles = get_custom_command_profile_list(db_session)
        for profile in profiles:
            choices.append((profile.id, profile.profile_name))
    except:
        logger.exception('fill_custom_command_profiles() hit exception')


def get_last_successful_inventory_elapsed_time(host):
    if host:
        # Last inventory successful time
        inventory_job = host.inventory_job[0]
        if inventory_job and inventory_job.last_successful_time:
            return time_difference_UTC(inventory_job.last_successful_time)

    return ''


def get_host_active_packages(hostname):
    """
    Returns a list of active/active-committed packages.  The list includes SMU/SP/Packages.
    """
    db_session = DBSession()
    host = get_host(db_session, hostname)

    result_list = []
    if host is not None:
        packages = db_session.query(Package).filter(
            and_(Package.host_id == host.id, or_(Package.state == PackageState.ACTIVE,
                                                 Package.state == PackageState.ACTIVE_COMMITTED))).all()
        for package in packages:
            result_list.append(package.name)

    return result_list


def get_host_inactive_packages(hostname):
    """
    Returns a list of inactive packages.  The list includes SMU/SP/Packages.
    """
    db_session = DBSession()
    host = get_host(db_session, hostname)

    result_list = []
    if host is not None:
        packages = db_session.query(Package).filter(
            and_(Package.host_id == host.id, Package.state == PackageState.INACTIVE)).all()
        for package in packages:
            result_list.append(package.name)

    return result_list


def get_host(db_session, hostname):
    return db_session.query(Host).filter(Host.hostname == hostname).first()


def get_host_by_id(db_session, id):
    return db_session.query(Host).filter(Host.id == id).first()


def get_host_list(db_session):
    return db_session.query(Host).order_by(Host.hostname.asc()).all()


def get_jump_host_by_id(db_session, id):
    return db_session.query(JumpHost).filter(JumpHost.id == id).first()


def get_jump_host(db_session, hostname):
    return db_session.query(JumpHost).filter(JumpHost.hostname == hostname).first()


def get_jump_host_list(db_session):
    return db_session.query(JumpHost).order_by(JumpHost.hostname.asc()).all()


def get_server(db_session, hostname):
    return db_session.query(Server).filter(Server.hostname == hostname).first()


def get_server_by_id(db_session, id):
    return db_session.query(Server).filter(Server.id == id).first()


def get_server_list(db_session):
    return db_session.query(Server).order_by(Server.hostname.asc()).all()


def get_custom_command_profile_by_id(db_session, profile_id):
    return db_session.query(CustomCommandProfile).filter(CustomCommandProfile.id == profile_id).first()


def get_custom_command_profile(db_session, profile_name):
    return db_session.query(CustomCommandProfile).filter(CustomCommandProfile.profile_name == profile_name).first()


def get_custom_command_profile_list(db_session):
    return db_session.query(CustomCommandProfile).order_by(CustomCommandProfile.profile_name.asc()).all()


def get_region(db_session, region_name):
    return db_session.query(Region).filter(Region.name == region_name).first()


def get_region_by_id(db_session, region_id):
    return db_session.query(Region).filter(Region.id == region_id).first()


def get_region_list(db_session):
    return db_session.query(Region).order_by(Region.name.asc()).all()


def get_software_profile_list(db_session):
    return db_session.query(SoftwareProfile).order_by(SoftwareProfile.name.asc()).all()


def get_software_profile(db_session, profile_name):
    return db_session.query(SoftwareProfile).filter(SoftwareProfile.name == profile_name).first()


def get_software_profile_by_id(db_session, id):
    return db_session.query(SoftwareProfile).filter(SoftwareProfile.id == id).first()


def get_hosts_by_software_profile_id(db_session, profile_id):
    return db_session.query(Host).filter(Host.software_profile_id == profile_id).order_by(Host.hostname.asc()).all()


def get_user(db_session, username):
    return db_session.query(User).filter(User.username == username).first()


def get_user_by_id(db_session, user_id):
    return db_session.query(User).filter(User.id == user_id).first()


def get_user_list(db_session):
    return db_session.query(User).order_by(User.fullname.asc()).all()


def get_install_job_by_id(db_session, job_id):
    return db_session.query(InstallJob).filter(InstallJob.id == job_id).first()


def get_smtp_server(db_session):
    return db_session.query(SMTPServer).first()


def can_check_reachability(current_user):
    return current_user.privilege == UserPrivilege.ADMIN or \
        current_user.privilege == UserPrivilege.NETWORK_ADMIN or \
        current_user.privilege == UserPrivilege.OPERATOR


def can_retrieve_software(current_user):
    return current_user.privilege == UserPrivilege.ADMIN or \
        current_user.privilege == UserPrivilege.NETWORK_ADMIN or \
        current_user.privilege == UserPrivilege.OPERATOR


def can_install(current_user):
    return current_user.privilege == UserPrivilege.ADMIN or \
        current_user.privilege == UserPrivilege.NETWORK_ADMIN or \
        current_user.privilege == UserPrivilege.OPERATOR


def can_delete_install(current_user):
    return current_user.privilege == UserPrivilege.ADMIN or \
        current_user.privilege == UserPrivilege.NETWORK_ADMIN or \
        current_user.privilege == UserPrivilege.OPERATOR


def can_edit_install(current_user):
    return current_user.privilege == UserPrivilege.ADMIN or \
        current_user.privilege == UserPrivilege.NETWORK_ADMIN or \
        current_user.privilege == UserPrivilege.OPERATOR


def can_create_user(current_user):
    return current_user.privilege == UserPrivilege.ADMIN


def can_edit(current_user):
    return can_create(current_user)


def can_delete(current_user):
    return can_create(current_user)


def can_create(current_user):
    return current_user.privilege == UserPrivilege.ADMIN or \
        current_user.privilege == UserPrivilege.NETWORK_ADMIN 


def get_return_url(request, default_url=None):
    """
    Returns the return_url encoded in the parameters
    """
    url = request.args.get('return_url')
    if url is None:
        url = default_url
    return url


def get_last_install_action(db_session, install_action, host_id):
    return db_session.query(InstallJob).filter(and_(InstallJob.install_action == install_action,
                                               InstallJob.host_id == host_id)). \
        order_by(InstallJob.scheduled_time.desc()).first()


def get_last_unfinished_install_action(db_session, install_action, host_id):
    return db_session.query(InstallJob).filter(and_(InstallJob.install_action == install_action,
                                                    InstallJob.host_id == host_id,
                                                    or_(InstallJob.status == None,
                                                        not_(InstallJob.status == JobStatus.FAILED)))). \
        order_by(InstallJob.scheduled_time.desc()).first()


def get_last_completed_or_failed_install_action(db_session, install_action, host_id):
    return db_session.query(InstallJobHistory).filter(and_(InstallJobHistory.install_action == install_action,
                                                           InstallJobHistory.host_id == host_id,
                                                           or_(InstallJobHistory.status == JobStatus.COMPLETED,
                                                               InstallJobHistory.status == JobStatus.FAILED))). \
        order_by(InstallJobHistory.status_time.desc()).first()


def get_install_job_dependency_completed(db_session, install_action, host_id):
    return db_session.query(InstallJobHistory).filter(and_(InstallJobHistory.install_action == install_action,
                                                           InstallJobHistory.host_id == host_id,
                                                           InstallJobHistory.status == JobStatus.COMPLETED)).all()


def create_or_update_host(db_session, hostname, region_id, location, roles, software_profile_id, connection_type,
                          host_or_ip, username, password, enable_password, port_number, jump_host_id, created_by,
                          host=None):

    hostname = check_acceptable_string(hostname)
    """ Create a new host in the Database """
    if host is None:
        host = Host(created_by=created_by)
        host.inventory_job.append(InventoryJob(status=JobStatus.SCHEDULED))
        host.context.append(HostContext())
        db_session.add(host)

    host.hostname = hostname
    host.region_id = region_id if region_id > 0 else None
    host.software_profile_id = software_profile_id if software_profile_id > 0 else None
    host.location = '' if location is None else remove_extra_spaces(location)
    host.roles = '' if roles is None else remove_extra_spaces(roles)
    host.connection_param = [ConnectionParam(
        # could have multiple IPs, separated by comma
        host_or_ip='' if host_or_ip is None else remove_extra_spaces(host_or_ip),
        username='' if username is None else remove_extra_spaces(username),
        password='' if password is None else remove_extra_spaces(password),
        enable_password='' if enable_password is None else remove_extra_spaces(enable_password),
        jump_host_id=jump_host_id if jump_host_id > 0 else None,
        connection_type=connection_type,
        # could have multiple ports, separated by comma
        port_number='' if port_number is None else remove_extra_spaces(port_number))]

    db_session.commit()

    return host


def get_host_list_by(db_session, platform, software_versions, region_ids, roles):
    """
    :param platform: Host platform
    :param software_versions: a list of software versions or 'ALL'
    :param region_ids: a list of region ids or 'ALL'
    :param roles: a list of roles or 'ALL'
    :return: a list of hosts that satisfied the criteria.
    """
    clauses = []
    clauses.append(Host.software_platform == platform)

    if 'ALL' not in software_versions:
        clauses.append(Host.software_version.in_(software_versions))

    if 'ALL' not in region_ids:
        clauses.append(Host.region_id.in_(region_ids))

    roles_list = [] if 'ALL' in roles else roles

    # Retrieve relevant hosts
    hosts = db_session.query(Host).filter(and_(*clauses)).order_by(Host.hostname.asc()).all()
 
    host_list = []
    for host in hosts:
        # Match on selected roles given by the user
        if not is_empty(roles_list):
            if not is_empty(host.roles):
                for role in host.roles.split(','):
                    if role in roles_list:
                        host_list.append(host)
                        break
        else:
            host_list.append(host)

    return host_list


def delete_host(db_session, hostname):
    host = get_host(db_session, hostname)
    if host is None:
        raise ValueNotFound("Host '{}' does not exist in the database.".format(hostname))

    host.delete(db_session)


def create_or_update_region(db_session, region_name, server_repositories, created_by, region=None):
    """
    :param db_session:
    :param name:
    :param server_repositories:
        assumes server_repositories is a comma-delimited string of valid server names that exist in CSM
    :param region:
    :return:
    """
    region_name = check_acceptable_string(region_name)

    if region is None:
        region = Region(created_by=created_by)
        db_session.add(region)

    region.name = region_name
    region.servers = []
    if not is_empty(server_repositories):
        for server_name in server_repositories.split(','):
            valid_server = get_server(db_session, server_name.strip())
            if valid_server is not None:
                region.servers.append(valid_server)
            else:
                raise ValueNotFound("Server repository '{}' does not exist in the database.".format(server_name))

    db_session.commit()

    return region


def get_host_software_profile_counts(db_session, software_profile_id):
    return db_session.query(Host).filter(Host.software_profile_id == software_profile_id).count()


def delete_software_profile(db_session, profile_name):

    software_profile = get_software_profile(db_session, profile_name)
    if software_profile is None:
        raise ValueNotFound("Software profile '{}' does not exist in the database.".format(profile_name))

    count = get_host_software_profile_counts(db_session, software_profile.id)

    # Older version of db does not perform check on
    # foreign key constrain, so do it programmatically here.
    if count > 0:
        raise OperationNotAllowed("Unable to delete software profile '{}'. "
                                  "Verify that it is not used by other hosts.".format(profile_name))

    db_session.delete(software_profile)
    db_session.commit()


def delete_region(db_session, name):
    region = get_region(db_session, name)
    if region is None:
        raise ValueNotFound("Region '{}' does not exist in the database.".format(name))

    count = db_session.query(Host).filter(
        Host.region_id == region.id).count()

    # Older version of db does not perform check on
    # foreign key constrain, so do it programmatically here.
    if count > 0:
        raise OperationNotAllowed("Unable to delete region '{}'. "
                                  "Verify that it is not used by other hosts.".format(name))

    db_session.delete(region)
    db_session.commit()


def create_or_update_custom_command_profile(db_session, profile_name, command_list, created_by,
                                            custom_command_profile=None):
    profile_name = check_acceptable_string(profile_name)

    if custom_command_profile is None:
        custom_command_profile = CustomCommandProfile(created_by=created_by)
        db_session.add(custom_command_profile)

    custom_command_profile.profile_name = profile_name
    custom_command_profile.command_list = '' if command_list is None else command_list
    db_session.commit()

    return custom_command_profile


def delete_custom_command_profile(db_session, profile_name):
    custom_command_profile = get_custom_command_profile(db_session, profile_name)

    if custom_command_profile is None:
        raise ValueNotFound("Custom command profile '{}' does not exist in the database.".format(profile_name))

    db_session.delete(custom_command_profile)
    db_session.commit()


def create_or_update_jump_host(db_session, hostname, connection_type, host_or_ip,
                               port_number, username, password, created_by, jumphost=None):

    hostname = check_acceptable_string(hostname)

    if jumphost is None:
        jumphost = JumpHost(created_by=created_by)
        db_session.add(jumphost)

    jumphost.hostname = hostname
    jumphost.host_or_ip = host_or_ip
    jumphost.username = username if username else ''
    jumphost.password = password if password else ''
    jumphost.connection_type = connection_type
    jumphost.port_number = port_number if port_number else ''

    db_session.commit()

    return jumphost


def delete_jump_host(db_session, hostname):
    jump_host = get_jump_host(db_session, hostname)
    if jump_host is None:
        raise ValueNotFound("Jump host '{}' does not exist in the database.".format(hostname))

    db_session.delete(jump_host)
    db_session.commit()


def create_or_update_server_repository(db_session, hostname, server_type, server_url, username, vrf,
                                       server_directory, password, destination_on_host, created_by, server=None):

    hostname = check_acceptable_string(hostname)

    if server is None:
        server = Server(created_by=created_by)
        db_session.add(server)

    server.hostname = hostname
    server.server_type = server_type
    server.server_url = server_url
    server.username = username
    server.password = password
    server.vrf = vrf if (server_type == ServerType.TFTP_SERVER or
                         server_type == ServerType.FTP_SERVER) else ''
    server.server_directory = server_directory
    server.destination_on_host = destination_on_host if server_type == ServerType.SCP_SERVER else ''

    db_session.commit()

    return server


def delete_server_repository(db_session, hostname):
    server = get_server(db_session, hostname)
    if server is None:
        raise ValueNotFound("Server repository '{}' does not exist in the database.".format(hostname))

    if len(server.regions) > 0:
        raise OperationNotAllowed("Unable to delete server repository '{}'. "
                                  "Verify that it is not used by other regions.".format(hostname))

    db_session.delete(server)
    db_session.commit()


def create_or_update_install_job(db_session, host_id, install_action, scheduled_time, software_packages=[],
                                 server_id=-1, server_directory='', custom_command_profile_ids=[], dependency=0,
                                 pending_downloads=[], created_by=None, install_job_data={}, install_job=None):

    if not type(software_packages) is list:
        raise ValueError('software_packages must be a list type.')

    if not type(custom_command_profile_ids) is list:
        raise ValueError('custom_command_profile_ids must be a list type.')

    if not type(pending_downloads) is list:
        raise ValueError('pending_downloads must be a list type.')

    if created_by is None:
        raise ValueError('created_by is None.')

    # This is a new install_job
    if install_job is None:
        install_job = InstallJob()
        install_job.host_id = host_id
        db_session.add(install_job)

    if len(install_job_data) > 0:
        # Copy keys to existing install job data field if the job exists
        for key, value in install_job_data.items():
            install_job.save_data(key, value)

    install_job.install_action = install_action

    if install_job.install_action == InstallAction.INSTALL_ADD and not is_empty(pending_downloads):
        install_job.pending_downloads = ','.join(pending_downloads)
    else:
        install_job.pending_downloads = ''

    install_job.scheduled_time = get_datetime(scheduled_time)

    # Only Install Add and Pre-Migrate should have server_id and server_directory
    if install_action == InstallAction.INSTALL_ADD or install_action == InstallAction.PRE_MIGRATE:
        install_job.server_id = int(server_id) if int(server_id) > 0 else None
        install_job.server_directory = server_directory
    else:
        install_job.server_id = None
        install_job.server_directory = ''

    install_job_packages = []

    # Only the following install actions should have software packages
    if install_action == InstallAction.INSTALL_ADD or \
        install_action == InstallAction.INSTALL_ACTIVATE or \
        install_action == InstallAction.INSTALL_REMOVE or \
        install_action == InstallAction.INSTALL_DEACTIVATE or \
        install_action == InstallAction.PRE_MIGRATE:

        for software_package in software_packages:
            if install_action == InstallAction.INSTALL_ADD:
                if is_file_acceptable_for_install_add(software_package):
                    install_job_packages.append(software_package)
            else:
                # Install Activate can have external or internal package names
                install_job_packages.append(software_package)

    install_job.packages = ','.join(install_job_packages)

    if dependency > 0:
        dependency_job = get_install_job_by_id(db_session, dependency)
        if dependency_job is not None:
            install_job.save_data('dependency_scheduled_time', get_datetime_string(dependency_job.scheduled_time))
        else:
            dependency = 0

    install_job.dependency = dependency if dependency > 0 else None

    user = get_user(db_session, created_by)

    install_job.created_by = created_by
    install_job.user_id = None if user is None else user.id

    if install_action == InstallAction.PRE_UPGRADE or install_action == InstallAction.POST_UPGRADE or \
        install_action == InstallAction.MIGRATION_AUDIT or install_action == InstallAction.PRE_MIGRATE or \
            install_action == InstallAction.MIGRATE_SYSTEM or install_action == InstallAction.POST_MIGRATE:
        install_job.custom_command_profile_ids = ','.join(custom_command_profile_ids) if custom_command_profile_ids else None

    # Resets the following fields
    install_job.status = JobStatus.SCHEDULED
    install_job.status_message = None
    install_job.status_time = None
    install_job.session_log = None
    install_job.trace = None

    if install_job.install_action != InstallAction.UNKNOWN:
        db_session.commit()

    # Creates download jobs if needed
    if install_job.install_action == InstallAction.INSTALL_ADD and \
        len(install_job.packages) > 0 and \
        len(install_job.pending_downloads) > 0:

        # Use the SMU name to derive the platform and release strings
        smu_list = install_job.packages.split(',')
        pending_downloads = install_job.pending_downloads.split(',')

        # Derives the platform and release using the first SMU name.
        platform, release = SMUInfoLoader.get_platform_and_release(smu_list)

        create_download_jobs(db_session, platform, release, pending_downloads,
                             install_job.server_id, install_job.server_directory, created_by)

    return install_job


def create_download_jobs(db_session, platform, release, pending_downloads, server_id, server_directory, created_by):
    """
    Pending downloads is an array of TAR files.
    """
    smu_meta = db_session.query(SMUMeta).filter(SMUMeta.platform_release == platform + '_' + release).first()
    if smu_meta is not None:
        for cco_filename in pending_downloads:
            # If the requested download_file is not in the download table, include it
            if not is_pending_on_download(db_session, cco_filename, server_id, server_directory):
                package_type = SMUInfoLoader.get_cco_file_package_type(db_session, cco_filename)

                if package_type == PackageType.SERVICE_PACK:
                    software_type_id = smu_meta.sp_software_type_id
                elif package_type == PackageType.SOFTWARE:
                    software_type_id = smu_meta.tar_software_type_id
                elif package_type == PackageType.SMU:
                    software_type_id = smu_meta.smu_software_type_id
                else:
                    # Best effort, it should not happen unless a SMU In-Transit is somehow get selected.
                    # All Posted software should have a cco file name entry.
                    software_type_id = smu_meta.smu_software_type_id

                download_job = DownloadJob(
                    cco_filename=cco_filename,
                    pid=smu_meta.pid,
                    mdf_id=smu_meta.mdf_id,
                    status=JobStatus.SCHEDULED,
                    software_type_id=software_type_id,
                    server_id=server_id,
                    server_directory=server_directory,
                    user_id=current_user.id,
                    created_by=created_by)

                db_session.add(download_job)
                db_session.commit()


def is_pending_on_download(db_session, filename, server_id, server_directory):
    download_job_key_dict = get_download_job_key_dict()
    download_job_key = get_download_job_key(current_user.id, filename, server_id, server_directory)

    if download_job_key in download_job_key_dict:
        download_job = download_job_key_dict[download_job_key]
        # Resurrect the download job
        if download_job is not None and (not download_job.status or download_job.status == JobStatus.FAILED):
            download_job.status = JobStatus.SCHEDULED
            download_job.status_time = None
            db_session.commit()
        return True

    return False


# Accepts an array containing paths to specific files
def download_session_logs(file_list, username):
    temp_user_dir = create_temp_user_directory(username)
    session_zip_path = os.path.normpath(os.path.join(temp_user_dir, "session_logs"))
    zip_file = os.path.join(session_zip_path, "session_logs.zip")
    create_directory(session_zip_path)
    make_file_writable(session_zip_path)

    zout = zipfile.ZipFile(zip_file, mode='w')
    for f in file_list:
        zout.write(os.path.normpath(f), os.path.basename(f))

    zout.close()

    return send_file(zip_file, as_attachment=True)


def get_last_completed_install_job_for_install_action(db_session, host_id, install_action):
    return db_session.query(InstallJobHistory). \
        filter(and_(InstallJobHistory.host_id == host_id,
                    InstallJobHistory.install_action == install_action,
                    InstallJobHistory.status == JobStatus.COMPLETED)). \
        order_by(InstallJobHistory.status_time.desc()).first()


def get_download_job_key(user_id, filename, server_id, server_directory):
    return "{}{}{}{}".format(user_id, filename, server_id, server_directory)


def get_conformance_report_by_id(db_session, id):
    return db_session.query(ConformanceReport).filter(ConformanceReport.id == id).first()


def get_host_platform_and_version_summary_tuples(db_session, region_id=0):
    if region_id == 0:
        result_tuples = db_session.query(Host.software_platform, Host.software_version, func.count(Host.software_version)).distinct()\
            .group_by(Host.software_platform, Host.software_version)
    else:
        result_tuples = db_session.query(Host.software_platform, Host.software_version, func.count(Host.software_version)).distinct()\
            .group_by(Host.software_platform, Host.software_version) \
            .filter(Host.region_id == region_id)

    return result_tuples