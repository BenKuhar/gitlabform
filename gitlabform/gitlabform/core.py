import argparse
import logging.config
import re
import traceback
import sys

from gitlabform.configuration import Configuration
from gitlabform.configuration.core import ConfigFileNotFoundException
from gitlabform.gitlab import GitLab
from gitlabform.gitlab.core import TestRequestFailedException
from gitlabform.gitlab.core import NotFoundException


class GitLabFormCore(object):

    def __init__(self):
        self.args = self.parse_args()
        self.set_log_level()
        self.gl, self.c = self.initialize_configuration_and_gitlab()

    def parse_args(self):

        parser = argparse.ArgumentParser(description='Easy configuration as code tool for GitLab'
                                                     ' using config in plain YAML.',
                                         formatter_class=argparse.ArgumentDefaultsHelpFormatter)

        parser.add_argument('project_or_group', help='Project name in "group/project" format OR single group name '
                                                     'OR "ALL" to run for all groups in configuration')

        parser.add_argument('-c', '--config', default='./config.yml', help='Config file path and filename', )

        group_ex = parser.add_mutually_exclusive_group()

        group_ex.add_argument('-v', '--verbose', action="store_true", help='Verbose mode')

        group_ex.add_argument('-d', '--debug', action="store_true", help='Verbose mode')

        parser.add_argument('--strict', '-s', action="store_true", help='Stop on missing branches')

        parser.add_argument('--start-from', dest='start_from', default=1, type=int,
                            help='Start processing projects from the given one '
                                 '(as numbered by "[x/y] Processing: group/project" messages)')

        return parser.parse_args()

    def set_log_level(self):

        logging.basicConfig()
        level = logging.WARNING
        if self.args.verbose:
            level = logging.INFO
        elif self.args.debug:
            level = logging.DEBUG
        logging.getLogger().setLevel(level)

        fmt = logging.Formatter("%(message)s")
        logging.getLogger().handlers[0].setFormatter(fmt)

    def initialize_configuration_and_gitlab(self):

        try:
            gl = GitLab(self.args.config)
            c = Configuration(self.args.config)
            return gl, c
        except ConfigFileNotFoundException as e:
            logging.fatal('Aborting - config file not found at: %s', e)
            sys.exit(1)
        except TestRequestFailedException as e:
            logging.fatal("Aborting - GitLab test request failed, details: '%s'", e)
            sys.exit(2)

    def main(self):
        projects_and_groups = self.get_projects_list()
        self.process_all(projects_and_groups)

    def get_projects_list(self):

        if self.args.project_or_group == "ALL":
            # all groups from config
            groups = self.c.get_groups()
            logging.warning('>>> Processing ALL groups from config: %s', ', '.join(groups))
            projects_and_groups = []
            for group in groups:
                projects_and_groups += self.gl.get_projects(group)
        elif not re.match(".*/.*", self.args.project_or_group):
            # single group
            group = self.args.project_or_group
            projects_and_groups = self.gl.get_projects(group)
        else:
            # single project
            project_and_group = self.args.project_or_group
            projects_and_groups = [project_and_group]

        project_to_skip = self.c.get_skip_projects()
        effective_projects_and_groups = [x for x in projects_and_groups if x not in project_to_skip]

        logging.warning('*** # of projects got from GitLab: %s', str(len(projects_and_groups)))
        logging.info('*** # Projects list from GitLab: %s', str(', '.join(sorted(projects_and_groups))))

        logging.warning('*** # of projects to skip: %s', str(len(project_to_skip)))
        logging.info('*** # Projects to skip: %s', str(', '.join(project_to_skip)))

        logging.warning('*** # of projects to really process: %s', str(len(effective_projects_and_groups)))
        logging.info('*** # Projects to actually process: %s', str(', '.join(effective_projects_and_groups)))

        return effective_projects_and_groups

    def process_all(self, projects_and_groups):

        i = 0

        for project_and_group in projects_and_groups:

            i += 1

            if i < self.args.start_from:
                logging.warning('$$$ [%s/%s] Skipping: %s...', i, len(projects_and_groups), project_and_group)
                continue

            logging.warning('* [%s/%s] Processing: %s', i, len(projects_and_groups), project_and_group)

            configuration = self.c.get_config_for_project(project_and_group)

            try:
                self.process_project_settings(project_and_group, configuration)
                self.process_deploy_keys(project_and_group, configuration)
                self.process_secret_variables(project_and_group, configuration)
                self.process_branches(project_and_group, configuration)
                self.process_services(project_and_group, configuration)
                self.process_files(project_and_group, configuration)
                self.process_hooks(project_and_group, configuration)

            except Exception as e:
                logging.error("+++ Error while processing '%s'", project_and_group)
                traceback.print_exc()

    def process_project_settings(self, project_and_group, configuration):
        if 'project_settings' in configuration:
            project_settings = configuration['project_settings']
            logging.debug("Project settings BEFORE: %s", self.gl.get_project_settings(project_and_group))
            logging.info("Setting project settings: %s", project_settings)
            self.gl.put_project_settings(project_and_group, project_settings)
            logging.debug("Project settings AFTER: %s", self.gl.get_project_settings(project_and_group))

    def process_deploy_keys(self, project_and_group, configuration):
        if 'deploy_keys' in configuration:
            logging.debug("Deploy keys BEFORE: %s", self.gl.get_deploy_keys(project_and_group))
            for deploy_key in sorted(configuration['deploy_keys']):
                logging.info("Setting deploy key: %s", deploy_key)
                self.gl.post_deploy_key(project_and_group, configuration['deploy_keys'][deploy_key])
            logging.debug("Deploy keys AFTER: %s", self.gl.get_deploy_keys(project_and_group))

    def process_secret_variables(self, project_and_group, configuration):
        if 'secret_variables' in configuration:
            logging.debug("Secret variables BEFORE: %s", self.gl.get_secret_variables(project_and_group))
            for secret_variable in sorted(configuration['secret_variables']):
                logging.info("Setting secret variable: %s", secret_variable)

                try:
                    current_value = \
                        self.gl.get_secret_variable(project_and_group,
                                                    configuration['secret_variables'][secret_variable]['key'])
                    if current_value != configuration['secret_variables'][secret_variable]['value']:
                        self.gl.put_secret_variable(project_and_group,
                                                    configuration['secret_variables'][secret_variable])
                except NotFoundException:
                    self.gl.post_secret_variable(project_and_group,
                                                 configuration['secret_variables'][secret_variable])

            logging.debug("Secret variables AFTER: %s", self.gl.get_secret_variables(project_and_group))

    def process_branches(self, project_and_group, configuration):
        if 'branches' in configuration:
            logging.info("Setting branches as protected/unprotected")
            for branch in sorted(configuration['branches']):
                try:
                    if configuration['branches'][branch]['protected']:
                        logging.debug("Setting branch '%s' as *protected*", branch)
                        # unprotect first to reset 'allowed to merge' and 'allowed to push' fields
                        self.gl.unprotect_branch(project_and_group, branch)
                        self.gl.protect_branch(project_and_group, branch,
                                               configuration['branches'][branch]['developers_can_push'],
                                               configuration['branches'][branch]['developers_can_merge'])
                    else:
                        logging.debug("Setting branch '%s' as unprotected", branch)
                        self.gl.unprotect_branch(project_and_group, branch)
                except NotFoundException:
                    logging.warning("! Branch '%s' not found when trying to set it as protected/unprotected",
                                    branch)
                    if self.args.strict:
                        exit(3)

    def process_services(self, project_and_group, configuration):
        if 'services' in configuration:
            logging.info("Setting services")
            for service in sorted(configuration['services']):
                if 'delete' in configuration['services'][service] \
                        and configuration['services'][service]['delete']:
                    logging.debug("Deleting service '%s'", service)
                    self.gl.delete_service(project_and_group, service)
                else:
                    logging.debug("Setting service '%s'", service)
                    self.gl.set_service(project_and_group, service, configuration['services'][service])

    def process_files(self, project_and_group, configuration):
        if 'files' in configuration:
            logging.info("Setting files")
            for file in sorted(configuration['files']):

                all_branches = self.gl.get_branches(project_and_group)
                if configuration['files'][file]['branches'] == 'all':
                    branches = sorted(all_branches)
                else:
                    branches = []
                    for branch in configuration['files'][file]['branches']:
                        if branch in all_branches:
                            branches.append(branch)
                        else:
                            logging.warning("! Branch '%s' not found, not processing file '%s' in it", branch,
                                            file)
                            if self.args.strict:
                                exit(3)

                for branch in branches:

                    # unprotect protected branch temporarily for operations below
                    if 'branches' in configuration \
                            and branch in configuration['branches'] \
                            and configuration['branches'][branch]['protected']:
                        logging.debug("> Temporarily unprotecting the branch for managing files in it...")
                        self.gl.unprotect_branch(project_and_group, branch)

                    if 'ignore' in configuration['files'][file] and configuration['files'][file]['ignore']:
                        logging.debug("Ignoring file '%s' in branch '%s'", file, branch)
                    elif 'delete' in configuration['files'][file] and configuration['files'][file]['delete']:
                        try:
                            self.gl.get_file(project_and_group, branch, file)
                            logging.debug("Deleting file '%s' in branch '%s'", file, branch)
                            self.gl.delete_file(project_and_group, branch, file,
                                                "Automated delete made by gitlabform")
                        except NotFoundException:
                            logging.debug("Not deleting file '%s' in branch '%s' (already doesn't exist)", file,
                                          branch)
                    else:
                        try:
                            current_content = self.gl.get_file(project_and_group, branch, file)
                            if current_content != configuration['files'][file]['content']:
                                if 'overwrite' in configuration['files'][file] \
                                        and configuration['files'][file]['overwrite']:
                                    logging.debug("Changing file '{0}' in branch '{1}'", file, branch)
                                    self.gl.set_file(project_and_group, branch, file,
                                                     configuration['files'][file]['content'],
                                                     "Automated change made by gitlabform")
                                else:
                                    logging.debug("Not changing file '%s' in branch '%s' "
                                                  "(overwrite flag not set)", file, branch)
                            else:
                                logging.debug("Not changing file '%s' in branch '%s' (it\'s content is already"
                                              " as provided)", file, branch)
                        except NotFoundException:
                            logging.debug("Creating file '%s' in branch '%s'", file, branch)
                            self.gl.add_file(project_and_group, branch, file,
                                             configuration['files'][file]['content'],
                                             "Automated add made by gitlabform")

                    # protect branch back after above operations
                    if 'branches' in configuration \
                            and branch in configuration['branches'] \
                            and configuration['branches'][branch]['protected']:
                        logging.debug("> Protecting the branch again.")
                        self.gl.protect_branch(project_and_group, branch,
                                               configuration['branches'][branch]['developers_can_push'],
                                               configuration['branches'][branch]['developers_can_merge'])

                    if 'only_first_branch' in configuration['files'][file] \
                            and configuration['files'][file]['only_first_branch']:
                        logging.info('Skipping other branches for this file, as configured.')
                        break

    def process_hooks(self, project_and_group, configuration):
        if 'hooks' in configuration:
            logging.info("Setting hooks")
            for hook in sorted(configuration['hooks']):

                if 'delete' in configuration['hooks'][hook] and configuration['hooks'][hook]['delete']:
                    hook_id = self.gl.get_hook_id(project_and_group, hook)
                    if hook_id:
                        logging.debug("Deleting hook '%s'", hook)
                        self.gl.delete_hook(project_and_group, hook_id)
                    else:
                        logging.debug("Not deleting hook '%s', because it doesn't exist", hook)
                else:
                    hook_id = self.gl.get_hook_id(project_and_group, hook)
                    if hook_id:
                        logging.debug("Changing existing hook '%s'", hook)
                        self.gl.put_hook(project_and_group, hook_id, hook, configuration['hooks'][hook])
                    else:
                        logging.debug("Creating hook '%s'", hook)
                        self.gl.post_hook(project_and_group, hook, configuration['hooks'][hook])
