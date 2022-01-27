import logging
import shutil
import sys
import os
import pathlib
from pathlib import Path
from typing import NewType, List

import sqlalchemy
import yaml
from sqlalchemy import MetaData
import datetime
import create_from_model.model_creation_services as create_from_model
from dotmap import DotMap

from api_logic_server_cli.create_from_model.model_creation_services import Resource

log = logging.getLogger(__file__)
log.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stderr)
formatter = logging.Formatter(f'%(name)s: %(message)s')     # lead tag - '%(name)s: %(message)s')
handler.setFormatter(formatter)
log.addHandler(handler)
log.propagate = True

# temp hacks for admin app migration to attributes

admin_attr_ordering = True
admin_parent_joins_implicit = True  # True => id's displayed as joins, False => explicit parent join attrs
admin_child_grids = False  # True => identify each child grid attr explicitly, False => use main grid definition
admin_relationships_with_parents = True

# have to monkey patch to work with WSL as workaround for https://bugs.python.org/issue38633
import errno, shutil
orig_copyxattr = shutil._copyxattr


def patched_copyxattr(src, dst, *, follow_symlinks=True):
    try:
        orig_copyxattr(src, dst, follow_symlinks=follow_symlinks)
    except OSError as ex:
        if ex.errno != errno.EACCES: raise


shutil._copyxattr = patched_copyxattr


#  MetaData = NewType('MetaData', object)
MetaDataTable = NewType('MetaDataTable', object)


class AdminCreator(object):
    """
    Iterate over model

    Create ui/admin/admin.yaml
    """

    _favorite_names_list = []  #: ["name", "description"]
    """
        array of substrings used to find favorite column name

        command line option to override per language, db conventions

        eg,
            name in English
            nom in French
    """
    _non_favorite_names_list = []
    non_favorite_names = "id"

    num_pages_generated = 0
    num_related = 0

    def __init__(self,
                 mod_gen: create_from_model.CreateFromModel,
                 host: str = "localhost",
                 port: str = "5656",
                 not_exposed: str = 'ProductDetails_V',
                 favorite_names: str = "name description",
                 non_favorite_names: str = "id"):
        self.mod_gen = mod_gen
        self.host = host
        self.port = port
        self.not_exposed = not_exposed
        self.favorite_names = favorite_names
        self.non_favorite_name = non_favorite_names
        self.multi_reln_exceptions = list()

        self.metadata = None
        self.engine = None
        self.session = None
        self.connection = None
        self.app = None
        self.admin_yaml = DotMap()
        self.max_list_columns = 8  # maybe make this a param

        self._non_favorite_names_list = self.non_favorite_names.split()
        self._favorite_names_list = self.favorite_names.split()

    def create_admin_application(self) -> str:
        """ main driver - loop through resources, write admin.yaml - with backup, nw customization
        """
        if self.mod_gen.command == "create-ui" or self.mod_gen.command.startswith("rebuild"):
            if self.mod_gen.command.startswith("rebuild"):
                print(".. .. ..Use existing ui/admin directory")
        else:
            self.create_admin_app(msg=".. .. ..Create ui/admin")

        cwd = os.getcwd()
        sys.path.append(cwd)

        self.admin_yaml.api_root = f'http://localhost:5656/api'
        if self.host != "localhost":
            if self.port !="":
                self.admin_yaml.api_root = f'http://{self.host}:{self.port}/api'
            else:
                self.admin_yaml.api_root = f'http://{self.host}/api'
        self.admin_yaml.resources = {}
        for each_resource_name in self.mod_gen.resource_list:
            each_resource = self.mod_gen.resource_list[each_resource_name]
            self.create_resource_in_admin(each_resource)

        self.create_about()
        self.create_info()
        self.create_settings()
        # self.doc_properties()

        admin_yaml_dict = self.admin_yaml.toDict()
        admin_yaml_dump = yaml.dump(admin_yaml_dict)
        if self.mod_gen.command != "create-ui":
            self.write_yaml_files(admin_yaml_dump)
        return admin_yaml_dump

    def create_resource_in_admin(self, resource: Resource):
        """ self.admin_yaml.resources += resource DotMap for given resource
        """
        resource_name = resource.name
        if self.do_process_resource(resource_name):
            new_resource = DotMap()
            self.num_pages_generated += 1
            new_resource.type = str(resource.name)
            new_resource.user_key = str(self.mod_gen.favorite_attribute_name(resource))

            self.create_attributes_in_owner(new_resource, resource, None)
            child_tabs = self.create_child_tabs(resource)
            if child_tabs:
                new_resource.tab_groups = child_tabs
            self.admin_yaml.resources[resource.table_name] = new_resource.toDict()

    def create_attributes_in_owner(self, owner: DotMap, resource: Resource, owner_resource: (None, Resource)):
        """ create attributes in owner (owner is a DotMap -- of resource, or tab)

            Order:
                attributes:  1 Favorite,  2 Joins,  3 Others / not favs,  4 Not Favs
                - label: ShipName*
                  name: ShipName
                  search: true
                - name: OrderDate
                - name: RequiredDate
                - name: Id
                - name: CustomerId
            """
        owner.attributes = []
        attributes_dict = []  # DotMap()
        processed_attributes = set()

        # Step 1 - favorite attribute
        favorite_attribute = resource.get_favorite_attribute()
        admin_attribute = self.create_admin_attribute(favorite_attribute)
        if admin_attribute is None:
            favorite_attribute = resource.attributes[0]
            admin_attribute = self.create_admin_attribute(favorite_attribute)
        processed_attributes.add(favorite_attribute.name)
        admin_attribute.search = True
        admin_attribute.sort = True
        admin_attribute.label = f"{self.cap_space(favorite_attribute.name)}*"
        attributes_dict.append(admin_attribute)

        # Step 2 - Parent Joins
        for each_parent in resource.parents:
            if admin_parent_joins_implicit:  # temp hack - just do the FK
                fk_pair = each_parent.parent_child_key_pairs[0]  # assume single-field keys
                fk_attr_name = fk_pair[1]
                resource_attribute = None
                for each_attribute in resource.attributes:
                    if each_attribute.name == fk_attr_name:
                        resource_attribute = each_attribute
                        break
                if resource_attribute is None:
                    raise Exception(f'System Error: unable to find {fk_attr_name} in {resource.name}')
                processed_attributes.add(fk_attr_name)
                admin_attribute = self.create_admin_attribute(resource_attribute)
                if admin_attribute is not None:
                    attributes_dict.append(admin_attribute)
            else:
                pass
                """  perhaps something like this:
                      - Location:     <— this is the parent resource name
                          fks:
                          - City       <- child FKs
                          - Country
                          attributes:  <- parent attrs to display
                          - name: city
                          - name: country

                """

        # Step 3 - Other fields, except non-favorites
        for each_attribute in resource.attributes:
            if each_attribute.name not in processed_attributes:
                if not each_attribute.non_favorite:
                    processed_attributes.add(each_attribute.name)
                    admin_attribute = self.create_admin_attribute(each_attribute)
                    if admin_attribute is not None:
                        attributes_dict.append(admin_attribute)

        # Step 4 - Non-favorites
        for each_attribute in resource.attributes:
            if each_attribute.name not in processed_attributes:
                if each_attribute.non_favorite:
                    processed_attributes.add(each_attribute.name)
                    admin_attribute = self.create_admin_attribute(each_attribute)
                    if admin_attribute is not None:
                        attributes_dict.append(admin_attribute)

        owner.attributes = attributes_dict

    @staticmethod
    def create_admin_attribute(resource_attribute) -> DotMap:
        attribute_name = resource_attribute if isinstance(resource_attribute, str) else resource_attribute.name
        nullable = True if isinstance(resource_attribute, str) else resource_attribute.nullable
        admin_attribute = DotMap()
        admin_attribute.name = str(attribute_name)
        if nullable == False:
            admin_attribute.required = True
        if attribute_name == "xShippedDate":
            log.debug("Good breakpoint location")
        if isinstance(resource_attribute, str) == True:
            log.debug("Just a string")
            raise Exception(f'System Error - expected resource_attribute, got string: {resource_attribute}')
        if not isinstance(resource_attribute, str):
            if resource_attribute.type in ["DECIMAL", "DATE"]:
                admin_attribute.type = resource_attribute.type
            if resource_attribute.type in ["NTEXT", "IMAGE"]:
                admin_attribute = None
        return admin_attribute  #.toDict()  hmm... sometimes a "shape" property slips in...?

    @staticmethod
    def cap_space(text):
        new_text = ' '
        for i, letter in enumerate(text):
            if i and letter.isupper():
                new_text += ' '
            new_text += letter
        return new_text

    def create_attributes_in_owner_zz(self, owner: DotMap, resource: Resource, owner_resource: (None, Resource)):
        """ create attributes in owner (owner is a DotMap -- of resource, or tab)

          Customer:
            attributes:
            - label: CompanyName*
              name: CompanyName
              search: true
            - name: ContactName
            - name: ContactTitle
        """
        owner.attributes = []
        attributes_dict = []  # DotMap()
        if admin_attr_ordering:
            attributes = self.mod_gen.get_show_attributes(resource)
        else:
            attributes = self.mod_gen.get_attributes(resource)
        for each_attribute in attributes:
            if "." not in each_attribute:   # not a parent join
                admin_attribute = DotMap()
                admin_attribute.name = each_attribute
                if each_attribute == self.mod_gen.favorite_attribute_name(resource):
                    admin_attribute.search = True
                    admin_attribute.label = f"{each_attribute}*"
                """
                if each_attribute.type in ["DECIMAL", "DATE"]:
                    admin_attribute.type = each_attribute.type
                """
                attributes_dict.append(admin_attribute)
            else:                           # parent join (disabled code - overwritten at end)
                relationship = self.new_relationship_to_parent(resource, each_attribute, owner_resource)
                if relationship is not None:  # skip redundant master join
                    rel = DotMap()
                    parent_role_name = each_attribute.split('.')[0]
                    rel[parent_role_name] = relationship.toDict()
                    owner.attributes.append(rel)
        owner.attributes = attributes_dict  # for now, just attrs, no parent joins since safrs-react autojoins FKs

    def new_relationship_to_parent(self, a_child_resource: Resource, parent_attribute_reference,
                                      a_master_parent_resource) -> (None, DotMap):
        """
        given a_child_table_def.parent_column_reference, create relationship: attrs, fKeys (for *js* client (no meta))

          Order:
            attributes:
            - ShipName
            - Amount
            - Location:
                fks:
                - City
                - Country
                attributes:
                - name: city
                - name: country

        :param a_child_resource: a child resource (not class), eg, Employees
        :param parent_attribute_reference: parent ref, eg, Department1.DepartmentName
        :param a_master_parent_resource: the master of master/detail - skip joins for this
        """
        parent_role_name = parent_attribute_reference.split('.')[0]  # careful - is role (class) name, not table name
        if a_master_parent_resource is not None and parent_role_name == a_master_parent_resource.name:
            skipped = f'avoid redundant master join - {a_child_resource}.{parent_attribute_reference}'
            log.debug(f'master object detected - {skipped}')
            return None
        relationship = DotMap()
        if len(self.mod_gen.resource_list) == 0:   # RARELY used - use_model is true (expose_existing not called)
            return self.new_relationship_to_parent_no_model(a_child_resource,
                                                            parent_attribute_reference, a_master_parent_resource)
        my_parents_list = a_child_resource.parents
        parent_relationship = None
        for each_parent_relationship in my_parents_list:
            if each_parent_relationship.parent_role_name == parent_role_name:
                parent_relationship = each_parent_relationship
                break
        if not parent_relationship:
            msg = f'Unable to find role for: {parent_attribute_reference}'
            relationship.error_unable_to_find_role = msg
            if parent_role_name not in self.multi_reln_exceptions:
                self.multi_reln_exceptions.append(parent_role_name)
                log.warning(f'Error - please search ui/admin/admin.yaml for: Unable to find role')
        relationship.resource = str(parent_relationship.parent_resource)  # redundant??
        relationship.attributes = []
        relationship.fks = []
        if a_child_resource.name == "Order":
            log.debug("Parents for special table - debug")
        for each_column in parent_relationship.parent_child_key_pairs:  # XXX FIXME
            # key_column = DotMap()
            # key_column.name = str(each_column)
            relationship.fks.append(str(each_column[1]))
        # todo - verify fullname is table name (e.g, multiple relns - emp.worksFor/onLoan)
        return relationship

    def create_child_tabs(self, resource: Resource) -> List:
        """
        build tabs for related children

        tab_groups:
          CustomerCustomerDemoList:
            direction: tomany
            fks:
            - CustomerTypeId
            resource: CustomerCustomerDemo
        """
        if len(self.mod_gen.resource_list) == 0:   # almost always, use_model false (we create)
            return self.create_child_tabs_no_model(resource)

        if resource.name == "Department":  # excellent breakpoint location
            log.debug(f'Relationships for {resource.name}')
        children_seen = set()
        tab_group = []
        for each_resource_relationship in resource.children:
            each_resource_tab = DotMap()
            self.num_related += 1
            each_child = each_resource_relationship.child_resource
            if each_child in children_seen:
                pass  # it's ok, we are using the child_role_name now
            children_seen.add(each_child)
            each_resource_tab.fks = []
            for each_pair in each_resource_relationship.parent_child_key_pairs:
                each_resource_tab.fks.append(str(each_pair[1]))
            each_child_resource = self.mod_gen.resource_list[each_child]
            each_resource_tab.resource = each_child_resource.table_name
            each_resource_tab.direction = "tomany"
            each_resource_tab.name = each_resource_relationship.child_role_name
            each_child_resource = self.mod_gen.resource_list[each_child]
            if admin_child_grids:
                self.create_attributes_in_owner(each_resource_tab, each_child_resource, resource)
            tab_group.append(each_resource_tab)  # disambiguate multi-relns, eg Employee OnLoan/WorksForDept
        if admin_relationships_with_parents:
            for each_resource_relationship in resource.parents:
                each_resource_tab = DotMap()
                each_parent = each_resource_relationship.parent_resource
                each_resource_tab.resource = str(each_parent)
                each_parent_resource = self.mod_gen.resource_list[each_parent]
                each_resource_tab.resource = each_parent_resource.table_name
                each_resource_tab.direction = "toone"
                each_resource_tab.fks = []
                for each_pair in each_resource_relationship.parent_child_key_pairs:
                    each_resource_tab.fks.append(str(each_pair[1]))
                each_resource_tab.name = each_resource_relationship.parent_role_name

                # tab_group[tab_name] = each_resource_tab  # disambiguate multi-relns, eg Employee OnLoan/WorksForDept
                tab_group.append(each_resource_tab)
        return tab_group

    def do_process_resource(self, resource_name: str)-> bool:
        """ filter out resources that are skipped by user, start with ab etc
        """
        if resource_name + " " in self.not_exposed:
            return False  # not_exposed: api.expose_object(models.{table_name})
        if "ProductDetails_V" in resource_name:
            log.debug("special table")  # should not occur (--noviews)
        if resource_name.startswith("ab_"):
            return False  # skip admin table: " + table_name + "\n
        elif 'sqlite_sequence' in resource_name:
            return False  # skip sqlite_sequence table: " + table_name + "\n
        elif resource_name is None:
            return False  # no class (view): " + table_name + "\n
        elif resource_name.startswith("Ab"):
            return False
        return True

    def create_child_tabs_no_model(self, a_table_def: MetaDataTable) -> DotMap:
        """
        Rarely used, now broken.  Ignore for now

        This approach is for cases where use_model specifies an existing model.

        In such cases, self.mod_gen.my_children_list is  None, so we need to get relns from db, inferring role names
        """
        all_tables = a_table_def.metadata.tables
        tab_group = DotMap()
        for each_possible_child_tuple in all_tables.items():
            each_possible_child = each_possible_child_tuple[1]
            parents = each_possible_child.foreign_keys
            if (a_table_def.name == "Customer" and
                    each_possible_child.name == "Order"):
                log.debug(a_table_def)
            for each_parent in parents:
                each_parent_name = each_parent.target_fullname
                loc_dot = each_parent_name.index(".")
                each_parent_name = each_parent_name[0:loc_dot]
                if each_parent_name == a_table_def.name:
                    self.num_related += 1
                    # self.yaml_lines.append(f'      - tab: {each_possible_child.name} List')
                    # self.yaml_lines.append(f'        resource: {each_possible_child.name}')
                    # self.yaml_lines.append(f'          fkeys:')
                    for each_foreign_key in each_parent.parent.foreign_keys:
                        for each_element in each_foreign_key.constraint.elements:
                            # self.yaml_lines.append(f'          - target: {each_element.column.key}')
                            child_table_name = each_element.parent.table.name
                            # self.yaml_lines.append(f'            source: {each_element.parent.name}')
                    # self.yaml_lines.append(f'          columns:')
                    columns = columns = self.mod_gen.get_show_columns(each_possible_child)
                    col_count = 0
                    for each_column in columns:
                        col_count += 1
                        if col_count > self.max_list_columns:
                            break
                        if "." not in each_column:
                            # self.yaml_lines.append(f'          - name: {each_column}')
                            pass
                        else:
                            pass
                            # self.create_object_reference(each_possible_child, each_column, 4, a_table_def)
        return tab_group

    def new_relationship_to_parent_no_model(self, a_child_table_def: MetaDataTable, parent_column_reference,
                                   a_master_parent_table_def) -> (None, DotMap):
        """
        Rarely used, now broken.  Ignore for now.

        This approach is for cases where use_model specifies an existing model.

        In such cases, self.mod_gen.my_children_list is  None, so we need to get relns from db, inferring role names
        """
        parent_role_name = parent_column_reference.split('.')[0]  # careful - is role (class) name, not table name
        relationship = DotMap()
        fkeys = a_child_table_def.foreign_key_constraints
        if a_child_table_def.name == "Employee":  # table Employees, class/role employee
            log.debug("Debug stop")
        found_fkey = False
        checked_keys = ""
        for each_fkey in fkeys:  # find fkey for parent_role_name
            referred_table: str = each_fkey.referred_table.key  # table name, eg, Employees
            referred_table = referred_table.lower()
            checked_keys += referred_table + " "
            if referred_table.startswith(parent_role_name.lower()):
                # self.yaml_lines.append(f'{tabs(num_tabs)}  - object:')
                # todo - verify fullname is table name (e.g, multiple relns - emp.worksFor/onLoan)
                # self.yaml_lines.append(f'{tabs(num_tabs)}    - type: {each_fkey.referred_table.fullname}')
                # self.yaml_lines.append(f'{tabs(num_tabs)}    - show_attributes:')
                # self.yaml_lines.append(f'{tabs(num_tabs)}    - key_attributes:')
                log.debug(f'got each_fkey: {str(each_fkey)}')
                for each_column in each_fkey.column_keys:
                    # self.yaml_lines.append(f'{tabs(num_tabs)}      - name: {each_column}')
                    pass
                found_fkey = True
        if not found_fkey:
            parent_table_name = parent_role_name
            if parent_table_name.endswith("1"):
                parent_table_name = parent_table_name[:-1]
                pass
            msg = f'Please specify references to {parent_table_name}'
            # self.yaml_lines.append(f'#{tabs(num_tabs)} - Multiple relationships detected -- {msg}')  FIXME
            if parent_role_name not in self.multi_reln_exceptions:
                self.multi_reln_exceptions.append(parent_role_name)
                log.warning(f'Alert - please search ui/admin/admin.yaml for: {msg}')
            # raise Exception(msg)
        return relationship

    def get_create_from_model_dir(self) -> Path:
        """
        :return: create_from_model dir, eg, /Users/val/dev/ApiLogicServer/create_from_model
        """
        path = Path(__file__)
        parent_path = path.parent
        parent_path = parent_path.parent
        return parent_path

    def write_yaml_files(self, admin_yaml):
        """ write admin.yaml, with backup, with additional nw customized backup
        """
        yaml_file_name = self.mod_gen.fix_win_path(self.mod_gen.project_directory + f'/ui/admin/admin.yaml')
        yaml_file_name = os.path.join(Path(self.mod_gen.project_directory), Path(f'ui/admin/admin.yaml'))
        enable_rebuild_unaltered = False
        ''' is not working on mac - always appears unaltered
            https://stackoverflow.com/questions/946967/get-file-creation-time-with-python-on-mac
        '''
        write_file = "Write"
        if self.mod_gen.command.startswith("rebuild"):
            write_file = "Rebuild - preserve"
            created_time = os.path.getctime(yaml_file_name)
            birth_time = os.stat(yaml_file_name).st_birthtime
            modified_time = os.path.getmtime(yaml_file_name)
            if enable_rebuild_unaltered and created_time == modified_time:
                write_file = "Rebuild - overwrite unaltered"
        if write_file == "Rebuild - preserve":
            print(f'.. .. ..{write_file} {yaml_file_name}')
        else:
            print(f'.. .. ..{write_file} {yaml_file_name}')
            with open(yaml_file_name, 'w') as yaml_file:
                yaml_file.write(admin_yaml)

        yaml_created_file_name = \
            self.mod_gen.fix_win_path(self.mod_gen.project_directory + f'/ui/admin/admin-created.yaml')
        with open(yaml_created_file_name, 'w') as yaml_copy_file:
            yaml_copy_file.write(admin_yaml)

        if self.mod_gen.nw_db_status in ["nw", "nw-"]:
            admin_custom_nw_file = open(
                os.path.dirname(os.path.realpath(__file__)) + "/templates/admin_custom_nw.yaml")
            admin_custom_nw = admin_custom_nw_file.read()
            nw_backup_file_name = \
                self.mod_gen.fix_win_path(self.mod_gen.project_directory + f'/ui/admin/admin_custom_nw.yaml')
            admin_file = open(nw_backup_file_name, 'w')
            admin_file.write(admin_custom_nw)
            admin_file.close()
            dev_temp_do_not_overwrite = False
            if not dev_temp_do_not_overwrite:
                print('.. .. ..Using customized admin_custom_nw.yaml - compare to admin-created.yaml')
                admin_file = open(yaml_file_name, 'w')
                admin_file.write(admin_custom_nw)
                admin_file.close()

    def create_settings(self):
        self.admin_yaml.settings = DotMap()
        self.admin_yaml.settings.max_list_columns = self.max_list_columns
        home_js = "http://localhost:5656/admin-app/home.js"
        if self.host != "localhost":
            if self.port !="":
                home_js = f'http://{self.host}:{self.port}/admin-app/home.js'
            else:
                home_js = f'http://{self.host}/admin-app/home.js'
        self.admin_yaml.settings.HomeJS = home_js
        return

    def create_about(self):
        self.admin_yaml.about = DotMap()
        self.admin_yaml.about.date = f'{str(datetime.datetime.now().strftime("%B %d, %Y %H:%M:%S"))}'
        self.admin_yaml.about.version = self.mod_gen.version
        self.admin_yaml.about.recent_changes = "works with modified safrs-react-admin"
        return

    def create_info(self):
        """
            info block - # tables, relns, [no-relns warning]
        """
        self.admin_yaml.info = DotMap()
        self.admin_yaml.info.number_tables = self.num_pages_generated
        self.admin_yaml.info.number_relationships = self.num_related
        if self.num_related == 0:
            # FIXME what to do self.yaml_lines.append(f'  warning: no_related_view')
            print(".. .. ..WARNING - no relationships detected - add them to your database or model")
            print(".. .. ..  See https://github.com/valhuber/LogicBank/wiki/Managing-Rules#database-design")

    def doc_properties(self):
        """ show non-automated properties in yaml, for users' quick reference
        """
        resource_props = DotMap()
        resource_props.menu = "False | name"
        resource_props.info = "long html / rich text"
        resource_props.allow_insert = "exp"
        resource_props.allow_update = "exp"
        resource_props.allow_delete = "exp"
        self.admin_yaml.properties_ref.resource = resource_props

        attr_props = DotMap()
        attr_props.search = "true | false"
        attr_props.label = "caption for display"
        attr_props.hidden = "exp"
        attr_props.group = "name"
        style_props = DotMap()
        style_props.font_weight = 0
        style_props.color = "blue"
        attr_props.style = style_props
        self.admin_yaml.properties_ref.attribute = attr_props

        tab_props = DotMap()
        tab_props.label = "text"
        tab_props.lookup = "boolean"
        self.admin_yaml.properties_ref.tab = tab_props

    def create_admin_app(self, msg: str = "", from_git: str = ""):
        """
        deep copy ApiLogicServer/create_from_model/admin -> project_directory/ui/admin

        :param msg: console log
        :param from_git: git url for source - override ApiLogicServer/create_from_model/admin (not impl)
        """
        from_proto_dir = from_git
        if from_proto_dir == "":
            from_proto_dir = pathlib.Path(self.get_create_from_model_dir()).\
                joinpath("create_from_model", "safrs-react-admin-npm-build")
        to_project_dir = pathlib.Path(self.mod_gen.project_directory).joinpath("ui", "safrs-react-admin")
        print(f'{msg} copy prototype admin project {from_proto_dir} -> {to_project_dir}')
        if not os.path.isdir(from_proto_dir):
            print(f'\n==> Error - prototype admin project... did you complete setup: https://github.com/valhuber/ApiLogicServer/wiki/Internals')
            print(".. Setup required.  Really.")
            exit(1)
        shutil.copytree(from_proto_dir, to_project_dir)

        os.mkdir(pathlib.Path(self.mod_gen.project_directory).joinpath("ui", "admin"))

        home_js_file = "home.js"  # get the welcome screen
        if self.mod_gen.nw_db_status in ["nw", "nw+"]:  # explains nw-specific customizations
            home_js_file = "home_nw.js"
        home_js = pathlib.Path(self.get_create_from_model_dir()).\
            joinpath("create_from_model", "templates", home_js_file)
        to_project_dir = pathlib.Path(self.mod_gen.project_directory).joinpath("ui", "admin")
        shutil.copyfile(home_js, to_project_dir.joinpath("home.js"))


def create(model_creation_services: create_from_model.CreateFromModel):
    """ called by ApiLogicServer CLI -- creates ui/admin application (ui/admin folder, admin.yaml)
    """
    admin_creator = AdminCreator(model_creation_services,
                                 host=model_creation_services.host, port=model_creation_services.port,
                                 not_exposed=model_creation_services.not_exposed + " ",
                                 favorite_names=model_creation_services.favorite_names,
                                 non_favorite_names=model_creation_services.non_favorite_names)
    return admin_creator.create_admin_application()

