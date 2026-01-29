"""
Server pref modal
Main interface
"""
import os
import libvirt
from textual.app import ComposeResult
from textual import on
from textual.worker import Worker
from textual.containers import ScrollableContainer, Horizontal, Vertical
from textual.widgets import (
        Button, Label,
        DataTable, Static,
        TabbedContent, TabPane, Tree
        )
from ..vm_queries import (
      get_all_vm_nvram_usage, get_all_vm_disk_usage,
      get_all_network_usage
      )
from ..libvirt_utils import get_network_info, _find_pool_by_path
from ..network_manager import (
      list_networks, get_vms_using_network, delete_network,
      set_network_active, set_network_autostart
      )
from .. import storage_manager
from ..storage_manager import list_storage_volumes

from ..constants import (
        AppCacheTimeout, ErrorMessages, SuccessMessages,
        ButtonLabels, WarningMessages,
        StaticText
        )
from .base_modals import BaseModal
from .network_modals import AddEditNetworkModal, NetworkXMLModal
from .disk_pool_modals import (
        AddPoolModal,
        CreateVolumeModal,
        MoveVolumeModal,
        AttachVolumeModal,
        )
from .utils_modals import ConfirmationDialog, ProgressModal
from .xml_modals import XMLDisplayModal
from .howto_network_modal import HowToNetworkModal


class ServerPrefModal(BaseModal[None]):
    """Modal screen for server preferences."""

    def __init__(self, uri: str | None = None) -> None:
        super().__init__()
        self.uri = uri
        self.storage_loaded = False
        self.network_loaded = False
        self.path_to_vm_list = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="server-pref-dialog",):
            yield Label(StaticText.SERVER_PREFERENCES, id="server-pref-title")
            yield Static(classes="button-separator")
            with TabbedContent(id="server-pref-tabs"):
                with TabPane("Network", id="tab-network"):
                    # Network content will be loaded on mount
                    pass
                with TabPane("Storage", id="tab-storage"):
                    # Storage content will be loaded on demand
                    pass

    def on_mount(self) -> None:
        uri_to_connect = self.uri
        if uri_to_connect is None:
            if len(self.app.active_uris) == 0:
                self.app.show_error_message(ErrorMessages.NOT_CONNECTED_TO_ANY_SERVER_PREFS)
                self.dismiss()
                return
            if len(self.app.active_uris) > 1:
                # This should not happen if the app logic uses the server selection modal
                self.app.show_error_message(ErrorMessages.MULTIPLE_SERVERS_ACTIVE_NONE_SELECTED)
                self.dismiss()
                return
            uri_to_connect = self.app.active_uris[0]

        self.conn = self.app.vm_service.connect(uri_to_connect)
        if not self.conn:
            self.app.show_error_message(ErrorMessages.FAILED_TO_GET_CONNECTION_PREFS_TEMPLATE.format(uri=uri_to_connect))
            self.dismiss()
            return

        # Get server hostname and update the title
        server_hostname = self.conn.getHostname()
        self.query_one("#server-pref-title", Label).update(f"Server Preferences ({server_hostname})")
        # Load network content by default (first tab)
        self._load_network_content()

    def _load_network_content(self) -> None:
        """Load network tab content."""
        if self.network_loaded:
            return

        network_pane = self.query_one("#tab-network", TabPane)

        # Clear existing content
        network_pane.remove_children()

        # Build network UI
        table = DataTable(id="networks-table", classes="networks-table", cursor_type="row")
        scroll_container = ScrollableContainer(table)

        button_container = Vertical(
            Horizontal(
                Button(ButtonLabels.DE_ACTIVE, id="toggle-net-active-btn", classes="toggle-detail-button",
                       variant="primary", disabled=True, tooltip="Activate or Deactivate the selected network."),
                Button(ButtonLabels.AUTOSTART, id="toggle-net-autostart-btn", classes="toggle-detail-button",
                       variant="primary", disabled=True, tooltip="Enable or Disable autostart for the selected network."),
            ),
            Horizontal(
                Button(ButtonLabels.ADD, id="add-net-btn", variant="success", classes="toggle-detail-button",
                       tooltip="Add a new network."),
                Button(ButtonLabels.EDIT, id="edit-net-btn", variant="success", classes="toggle-detail-button",
                       disabled=True, tooltip="Edit the selected network."),
                Button(ButtonLabels.VIEW, id="view-net-btn", variant="success", classes="toggle-detail-button",
                       disabled=True, tooltip="View XML details of the selected network."),
                Button(ButtonLabels.DELETE, id="delete-net-btn", variant="error", classes="toggle-detail-button",
                       disabled=True, tooltip="Delete the selected network."),
                Button(ButtonLabels.HELP, id="help-net-btn", variant="success", classes="toggle-detail-button",
                       tooltip="Show network configuration help."),
            ),
            classes="server-pref-button"
        )

        # Mount everything together
        network_pane.mount(scroll_container, button_container)

        # Load data after widgets are mounted
        self.call_after_refresh(self._load_networks)
        self.network_loaded = True

    def _load_storage_content(self) -> None:
        """Load storage tab content."""
        if self.storage_loaded:
            return

        # Prepare disk/nvram/overlay maps for storage (only when loading Storage tab)
        # This is done here to avoid slowing down the initial modal load
        if not self.path_to_vm_list:
            try:
                num_vms = self.conn.numOfDomains() + self.conn.numOfDefinedDomains()
            except libvirt.libvirtError:
                num_vms = 0


            if num_vms > AppCacheTimeout.DONT_DISPLAY_DISK_USAGE:
                self.app.show_warning_message(WarningMessages.TOO_MANY_VMS_DISK_USAGE_WARNING_TEMPLATE.format(count=AppCacheTimeout.DONT_DISPLAY_DISK_USAGE))
            else:
                #self.app.show_warning_message(WarningMessages.RUNNING_DISK_USAGE_SCAN_WARNING)
                disk_map = get_all_vm_disk_usage(self.conn)
                nvram_map = get_all_vm_nvram_usage(self.conn)
                #overlay_map = get_all_vm_overlay_usage(self.conn)

                # Merge the dictionaries
                self.path_to_vm_list = disk_map.copy()
                for path, vm_names in nvram_map.items():
                    if path in self.path_to_vm_list:
                        # Combine lists and remove duplicates
                        self.path_to_vm_list[path] = list(set(self.path_to_vm_list[path] + vm_names))
                    else:
                        self.path_to_vm_list[path] = vm_names

        storage_pane = self.query_one("#tab-storage", TabPane)

        # Clear existing content
        storage_pane.remove_children()

        # Build storage UI
        tree = Tree("Storage Pools", id="storage-tree")
        scroll_container = ScrollableContainer(tree)

        button_container = Vertical(
            Horizontal(
                Button(id="toggle-active-pool-btn", variant="primary", classes="toggle-detail-button",
                       tooltip="Activate or Deactivate the selected storage pool."),
                Button(id="toggle-autostart-pool-btn", variant="primary", classes="toggle-detail-button",
                       tooltip="Enable or Disable autostart for the selected storage pool."),
                Vertical(
                    Button(ButtonLabels.ADD_POOL, id="add-pool-btn", variant="success", classes="toggle-detail-button",
                           tooltip="Add a new storage pool."),
                    Button(ButtonLabels.DELETE_POOL, id="del-pool-btn", variant="error", classes="toggle-detail-button",
                           tooltip="Delete the selected storage pool."),
                ),
                Vertical(
                    Button(ButtonLabels.NEW_VOLUME, id="add-vol-btn", variant="success", classes="toggle-detail-button",
                           tooltip="Create a new volume in the selected pool."),
                    Button(ButtonLabels.ATTACH_VOL, id="attach-vol-btn", variant="success", classes="toggle-detail-button",
                           tooltip="Attach an existing disk file as a volume."),
                    Button(ButtonLabels.MOVE_VOLUME, id="move-vol-btn", variant="success", classes="toggle-detail-button",
                           tooltip="Move the selected volume to another pool."),
                    Button(ButtonLabels.DELETE_VOLUME, id="del-vol-btn", variant="error", classes="toggle-detail-button",
                           tooltip="Delete the selected volume."),
                ),
                Vertical(
                    Button(ButtonLabels.VIEW_XML, id="view-storage-xml-btn", variant="primary", classes="toggle-detail-button",
                           tooltip="View XML details of the selected pool or volume."),
                    Button(ButtonLabels.EDIT_XML, id="edit-pool-xml-btn", variant="primary", classes="toggle-detail-button",
                           tooltip="Edit XML of the selected pool."),
                ),
            ),
            classes="server-pref-button"
        )

        storage_pane.mount(scroll_container, button_container)

        self.call_after_refresh(self._load_storage_pools)
        self.storage_loaded = True

    def _unload_network_content(self) -> None:
        """Remove network tab content."""
        if not self.network_loaded:
            return

        network_pane = self.query_one("#tab-network", TabPane)
        network_pane.remove_children()
        self.network_loaded = False

    def _unload_storage_content(self) -> None:
        """Remove storage tab content."""
        if not self.storage_loaded:
            return

        storage_pane = self.query_one("#tab-storage", TabPane)
        storage_pane.remove_children()
        self.storage_loaded = False

    @on(TabbedContent.TabActivated)
    def on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Handle tab changes to load/unload content dynamically."""
        active_pane = event.pane
        active_tab_id = active_pane.id if active_pane else None

        if active_tab_id == "tab-network":
            # Load network content, unload storage
            self._load_network_content()
            self._unload_storage_content()
        elif active_tab_id == "tab-storage":
            # Load storage content, unload network
            self._load_storage_content()
            self._unload_network_content()

    def _load_storage_pools(self, expand_pools: list[str] | None = None, select_pool: str | None = None) -> None:
        """Load storage pools into the tree view."""
        tree: Tree[dict] = self.query_one("#storage-tree")
        tree.clear()
        tree.root.data = {"type": "root"}
        pools = storage_manager.list_storage_pools(self.conn)
        node_to_select = None
        for pool_data in pools:
            try:
                pool_name = pool_data['name']
                status = pool_data['status']
                autostart = "autostart" if pool_data['autostart'] else "no autostart"
                label = f"{pool_name} [{status}, {autostart}]"
                pool_node = tree.root.add(label, data=pool_data)
                pool_node.data["type"] = "pool"
                # Add a dummy node to make the pool node expandable
                pool_node.add_leaf("Loading volumes...")

                if expand_pools and pool_name in expand_pools:
                    self.app.call_later(pool_node.expand)

                if select_pool and pool_name == select_pool:
                    node_to_select = pool_node
            except libvirt.libvirtError:
                # Handle cases where pool might be listed but inaccessible (e.g. NFS down)
                pool_name = pool_data.get('name', 'Unknown')
                label = f"{pool_name} [unavailable]"
                pool_node = tree.root.add(label, data=pool_data)
                pool_node.data["type"] = "pool"
                pool_node.add_leaf("Pool unavailable")

        if node_to_select:
            self.app.call_later(tree.select_node, node_to_select)

    def _load_networks(self):
        table = self.query_one("#networks-table", DataTable)

        if not table.columns:
            table.add_column("Name", key="name")
            table.add_column("Mode", key="mode")
            table.add_column("Active", key="active")
            table.add_column("Autostart", key="autostart")
            table.add_column("Used By", key="used_by")

        table.clear()

        #self.app.show_warning_message(WarningMessages.RUNNING_NETWORK_USAGE_SCAN_WARNING)
        network_usage = get_all_network_usage(self.conn)
        self.networks_list = list_networks(self.conn)

        for net in self.networks_list:
            vms_str = ", ".join(network_usage.get(net['name'], [])) or "Not in use"
            active_str = "✔️" if net['active'] else "❌"
            autostart_str = "✔️" if net['autostart'] else "❌"

            table.add_row(
                net['name'],
                net['mode'],
                active_str,
                autostart_str,
                vms_str,
                key=net['name']
            )

    @on(Tree.NodeExpanded)
    async def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        """Load child nodes when a node is expanded."""
        node = event.node
        node_data = node.data
        if not node_data or node_data.get("type") != "pool":
            return

        # If it's the first time expanding, the only child is the dummy "Loading..."
        if len(node.children) == 1 and node.children[0].data is None:
            node.remove_children()
            pool = node_data.get('pool')
            # Check cached status instead of calling pool.isActive() to avoid blocking
            if pool and node_data.get('status') == 'active':
                volumes = storage_manager.list_storage_volumes(pool)
                for vol_data in volumes:
                    vol_name = vol_data['name']
                    vol_path = vol_data['volume'].path()
                    capacity_gb = round(vol_data['capacity'] / (1024**3), 2)

                    vm_names = self.path_to_vm_list.get(vol_path, [])
                    usage_info = f" (in use by {', '.join(vm_names)})" if vm_names else ""

                    label = f"{vol_name} ({capacity_gb} GB){usage_info}"
                    child_node = node.add(label, data=vol_data)
                    child_node.data["type"] = "volume"
                    child_node.allow_expand = False
            else:
                # Handle case where pool is not active
                node.add_leaf("Pool is not active")


    @on(Tree.NodeSelected)
    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Handle node selection to enable/disable buttons."""
        node = event.node
        node_data = node.data if node else None

        is_pool = bool(node_data and node_data.get("type") == "pool")
        is_volume = bool(node_data and node_data.get("type") == "volume")

        toggle_active_btn = self.query_one("#toggle-active-pool-btn")
        toggle_autostart_btn = self.query_one("#toggle-autostart-pool-btn")
        del_pool_btn = self.query_one("#del-pool-btn")
        add_pool_btn = self.query_one("#add-pool-btn")

        toggle_active_btn.display = is_pool
        toggle_autostart_btn.display = is_pool
        del_pool_btn.display = is_pool
        add_pool_btn.display = not is_volume

        self.query_one("#del-vol-btn").display = is_volume
        self.query_one("#move-vol-btn").display = is_volume
        self.query_one("#view-storage-xml-btn").display = is_pool or is_volume
        self.query_one("#edit-pool-xml-btn").display = is_pool

        if is_pool:
            is_active = node_data.get('status') == 'active'
            has_autostart = node_data.get('autostart', False)
            toggle_active_btn.label = "Deactivate" if is_active else "Activate"
            toggle_autostart_btn.label = "Autostart Off" if has_autostart else "Autostart On"

        self.query_one("#add-vol-btn").display = is_pool and is_active
        self.query_one("#attach-vol-btn").display = is_pool and is_active

    @on(Button.Pressed, "#view-storage-xml-btn")
    def on_view_storage_xml_button_pressed(self, event: Button.Pressed) -> None:
        """Handle the View XML button press to show pool/volume XML."""
        tree: Tree[dict] = self.query_one("#storage-tree")
        if not tree.cursor_node or not tree.cursor_node.data:
            return

        node_data = tree.cursor_node.data
        node_type = node_data.get("type")

        target_obj = None
        if node_type == "pool":
            target_obj = node_data.get('pool')
        elif node_type == "volume":
            target_obj = node_data.get('volume')
        else:
            return

        if not target_obj:
            self.app.show_error_message(ErrorMessages.COULD_NOT_FIND_OBJECT_XML)
            return

        try:
            xml_content = target_obj.XMLDesc(0)
            self.app.push_screen(XMLDisplayModal(xml_content, read_only=True))
        except libvirt.libvirtError as e:
            self.app.show_error_message(ErrorMessages.ERROR_GETTING_XML_FOR_TYPE_TEMPLATE.format(obj_type=node_type, error=e))

    @on(Button.Pressed, "#edit-pool-xml-btn")
    def on_edit_pool_xml_button_pressed(self, event: Button.Pressed) -> None:
        """Handle the Edit Pool XML button press."""
        tree: Tree[dict] = self.query_one("#storage-tree")
        if not tree.cursor_node or not tree.cursor_node.data:
            return

        node_data = tree.cursor_node.data
        if node_data.get("type") != "pool":
            return

        pool = node_data.get('pool')
        if not pool:
            self.app.show_error_message(ErrorMessages.COULD_NOT_FIND_POOL_EDIT)
            return

        if node_data.get('status') == 'active':
            self.app.show_error_message(ErrorMessages.POOL_MUST_BE_INACTIVE_FOR_XML_EDIT)
            return

        def on_edit_confirm(confirmed: bool) -> None:
            if not confirmed:
                return

            try:
                xml_content = pool.XMLDesc(0)
            except libvirt.libvirtError as e:
                self.app.show_error_message(ErrorMessages.ERROR_GETTING_XML_FOR_POOL_TEMPLATE.format(error=e))
                return

            def on_xml_save(new_xml: str | None) -> None:
                if new_xml is None:
                    return  # User cancelled

                try:
                    # Redefine the pool with the new XML
                    self.conn.storagePoolDefineXML(new_xml, 0)
                    self.app.show_success_message(SuccessMessages.STORAGE_POOL_UPDATED_SUCCESSFULLY_TEMPLATE.format(pool_name=pool.name()))
                    storage_manager.list_storage_pools.cache_clear()
                    self._load_storage_pools()  # Refresh the tree
                except libvirt.libvirtError as e:
                    self.app.show_error_message(ErrorMessages.ERROR_UPDATING_POOL_XML_TEMPLATE.format(error=e))
                except Exception as e:
                    self.app.show_error_message(ErrorMessages.UNEXPECTED_ERROR_OCCURRED_TEMPLATE_XML.format(error=e))

            self.app.push_screen(XMLDisplayModal(xml_content, read_only=False), on_xml_save)

        warning_message = ErrorMessages.EDIT_POOL_XML_WARNING
        self.app.push_screen(ConfirmationDialog(warning_message), on_edit_confirm)

    @on(Button.Pressed, "#toggle-active-pool-btn")
    def on_toggle_active_pool_button_pressed(self, event: Button.Pressed) -> None:
        """Handle pool activation/deactivation."""
        tree: Tree[dict] = self.query_one("#storage-tree")
        if not tree.cursor_node or not tree.cursor_node.data:
            return

        node_data = tree.cursor_node.data
        if node_data.get("type") != "pool":
            return

        pool = node_data.get('pool')
        pool_name = node_data.get('name')
        is_active = node_data.get('status') == 'active'
        try:
            storage_manager.set_pool_active(pool, not is_active)
            status = 'inactive' if is_active else 'active'
            self.app.show_success_message(SuccessMessages.POOL_ACTIVATION_CHANGE_TEMPLATE.format(pool_name=pool.name(), status=status))
            node_data['status'] = 'inactive' if is_active else 'active'
            storage_manager.list_storage_pools.cache_clear()
            self._load_storage_pools(select_pool=pool_name) # Refresh the tree
        except Exception as e:
            self.app.show_error_message(ErrorMessages.ERROR_TOGGLE_POOL_ACTIVE_TEMPLATE.format(error=e))

    @on(Button.Pressed, "#toggle-autostart-pool-btn")
    def on_toggle_autostart_pool_button_pressed(self, event: Button.Pressed) -> None:
        """Handle pool autostart toggling."""
        tree: Tree[dict] = self.query_one("#storage-tree")
        if not tree.cursor_node or not tree.cursor_node.data:
            return

        node_data = tree.cursor_node.data
        if node_data.get("type") != "pool":
            return

        pool = node_data.get('pool')
        pool_name = node_data.get('name')
        has_autostart = node_data.get('autostart', False)
        try:
            storage_manager.set_pool_autostart(pool, not has_autostart)
            status = 'off' if has_autostart else 'on'
            self.app.show_success_message(SuccessMessages.POOL_AUTOSTART_CHANGE_TEMPLATE.format(pool_name=pool.name(), status=status))
            node_data['autostart'] = not has_autostart
            storage_manager.list_storage_pools.cache_clear()
            self._load_storage_pools(select_pool=pool_name) # Refresh the tree
        except Exception as e:
            self.app.show_error_message(ErrorMessages.ERROR_TOGGLE_POOL_AUTOSTART_TEMPLATE.format(error=e))

    @on(Button.Pressed, "#add-vol-btn")
    def on_add_volume_button_pressed(self, event: Button.Pressed) -> None:
        tree: Tree[dict] = self.query_one("#storage-tree")
        if not tree.cursor_node or not tree.cursor_node.data:
            return

        node_data = tree.cursor_node.data
        if node_data.get("type") != "pool":
            return

        pool = node_data.get('pool')
        pool_node = tree.cursor_node

        def on_create(result: dict | None) -> None:
            if result:
                try:
                    storage_manager.create_volume(
                        pool,
                        result['name'],
                        result['size_gb'],
                        result['format']
                    )
                    self.app.show_success_message(SuccessMessages.VOLUME_CREATED_SUCCESSFULLY_TEMPLATE.format(name=result['name'], size_gb=result['size_gb'], format=result['format']))
                    storage_manager.list_storage_volumes.cache_clear()
                    pool_node.add_leaf(result['name'])

                except Exception as e:
                    self.app.show_error_message(str(e))

        self.app.push_screen(CreateVolumeModal(), on_create)

    @on(Button.Pressed, "#attach-vol-btn")
    def on_attach_volume_button_pressed(self, event: Button.Pressed) -> None:
        def on_attach(result: dict | None) -> None:
            if not result:
                return

            volume_path = result.get('path')
            if not volume_path or not os.path.exists(volume_path):
                self.app.show_error_message(ErrorMessages.INVALID_OR_NON_EXISTENT_PATH_TEMPLATE.format(path=volume_path))
                return

            volume_dir = os.path.dirname(volume_path)
            existing_pool = _find_pool_by_path(self.conn, volume_dir)

            if existing_pool:
                try:
                    existing_pool.refresh(0)
                    storage_manager.list_storage_volumes.cache_clear()
                    self.app.show_success_message(
                        SuccessMessages.POOL_MANAGED_BY_EXISTING_POOL_TEMPLATE.format(pool_name=existing_pool.name())
                    )
                except libvirt.libvirtError as e:
                    self.app.show_error_message(ErrorMessages.ERROR_REFRESHING_POOL_TEMPLATE.format(error=e))
                
                self._load_storage_pools(expand_pools=[existing_pool.name()])
                return

            new_pool_name = f"pool_{os.path.basename(volume_dir)}".replace(" ", "_").replace(".", "_")

            def on_confirm_create(confirmed: bool) -> None:
                if confirmed:
                    try:
                        storage_manager.create_storage_pool(self.conn, new_pool_name, 'dir', volume_dir)
                        self.app.show_success_message(
                            SuccessMessages.STORAGE_POOL_CREATED_TEMPLATE.format(name=new_pool_name)
                        )
                        storage_manager.list_storage_pools.cache_clear()
                        self._load_storage_pools(expand_pools=[new_pool_name])
                    except Exception as e:
                        self.app.show_error_message(ErrorMessages.ERROR_CREATING_STORAGE_POOL_FOR_DIR_TEMPLATE.format(error=e))

            self.app.push_screen(
                ConfirmationDialog(
                    ErrorMessages.NO_POOL_FOR_DIRECTORY_CONFIRM_TEMPLATE.format(directory=volume_dir, pool_name=new_pool_name)
                ),
                on_confirm_create
            )

        self.app.push_screen(AttachVolumeModal(), on_attach)

    @on(Button.Pressed, "#add-pool-btn")
    def on_add_pool_button_pressed(self, event: Button.Pressed) -> None:
        def on_create(success: bool | None) -> None:
            if success:
                storage_manager.list_storage_pools.cache_clear()
                self._load_storage_pools()

        self.app.push_screen(AddPoolModal(self.conn), on_create)

    @on(Button.Pressed, "#del-pool-btn")
    def on_delete_pool_button_pressed(self, event: Button.Pressed) -> None:
        tree: Tree[dict] = self.query_one("#storage-tree")
        if not tree.cursor_node or not tree.cursor_node.data:
            return

        node_data = tree.cursor_node.data
        if node_data.get("type") != "pool":
            return

        pool_name = node_data.get('name')
        pool = node_data.get('pool')

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                try:
                    storage_manager.delete_storage_pool(pool)
                    self.app.show_success_message(SuccessMessages.STORAGE_POOL_DELETED_SUCCESSFULLY_TEMPLATE.format(pool_name=pool_name))
                    storage_manager.list_storage_pools.cache_clear()
                    self._load_storage_pools() # Refresh the tree
                except Exception as e:
                    self.app.show_error_message(ErrorMessages.UNEXPECTED_ERROR_OCCURRED_TEMPLATE_XML.format(error=e))

        self.app.push_screen(
                ConfirmationDialog(ErrorMessages.DELETE_STORAGE_POOL_CONFIRMATION_TEMPLATE.format(pool_name=pool_name)), on_confirm)

    @on(Button.Pressed, "#del-vol-btn")
    def on_delete_volume_button_pressed(self, event: Button.Pressed) -> None:
        tree: Tree[dict] = self.query_one("#storage-tree")
        if not tree.cursor_node or not tree.cursor_node.data:
            return

        node_data = tree.cursor_node.data
        if node_data.get("type") != "volume":
            return

        vol_name = node_data.get('name')
        vol = node_data.get('volume')

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                try:
                    storage_manager.delete_volume(vol)
                    self.app.show_success_message(SuccessMessages.VOLUME_DELETED_SUCCESSFULLY_TEMPLATE.format(volume_name=vol_name))
                    # Clear the cache to force a refresh
                    list_storage_volumes.cache_clear()
                    # Refresh the parent node
                    parent_node = tree.cursor_node.parent
                    tree.cursor_node.remove()
                    if parent_node and not parent_node.children:
                        parent_node.add_leaf("No volumes")

                except Exception as e:
                    self.app.show_error_message(ErrorMessages.UNEXPECTED_ERROR_OCCURRED_TEMPLATE_XML.format(error=e))

        self.app.push_screen(
                ConfirmationDialog(ErrorMessages.DELETE_VOLUME_CONFIRMATION_TEMPLATE.format(vol_name=vol_name)),
            on_confirm
        )

    @on(Button.Pressed, "#move-vol-btn")
    def on_move_volume_button_pressed(self, event: Button.Pressed) -> None:
        tree: Tree[dict] = self.query_one("#storage-tree")
        if not tree.cursor_node or not tree.cursor_node.data:
            return

        node_data = tree.cursor_node.data
        if node_data.get("type") != "volume":
            return

        volume_name = node_data.get('name')
        if not tree.cursor_node.parent or not tree.cursor_node.parent.data:
            self.app.show_error_message(ErrorMessages.COULD_NOT_DETERMINE_SOURCE_POOL)
            return
        source_pool_name = tree.cursor_node.parent.data.get('name')

        def on_move(result: dict | None) -> None:
            if not result:
                return

            dest_pool_name = result['dest_pool']
            new_volume_name = result['new_name']

            progress_modal = ProgressModal(title=f"Moving {volume_name}...")
            self.app.push_screen(progress_modal)

            def progress_callback(progress: float):
                self.app.call_from_thread(progress_modal.update_progress, progress=progress)

            def log_callback(message: str):
                self.app.call_from_thread(progress_modal.add_log, message)

            def find_node_by_label(tree, label_text):
                """Find a node in the tree by its label."""
                def search_node(node):
                    # Check if current node's label matches
                    if str(node.label) == label_text:
                        return node
                    # Search in children recursively
                    for child in node.children:
                        result = search_node(child)
                    if result:
                        return result
                    return None

                return search_node(tree.root)

            def do_move():
                try:
                    updated_vms = storage_manager.move_volume(
                        self.conn,
                        source_pool_name,
                        dest_pool_name,
                        volume_name,
                        new_volume_name,
                        progress_callback=progress_callback,
                        log_callback=log_callback
                    )
                    # Refresh the parent node
                    parent_node = tree.cursor_node.parent
                    tree.cursor_node.remove()
                    if parent_node and not parent_node.children:
                        parent_node.add_leaf("No volumes")
                    target_node = find_node_by_label(tree, dest_pool_name)
                    target_node.add_leaf(new_volume_name)
                    self._load_storage_pools()

                    return {
                        "message": SuccessMessages.VOLUME_MOVED_SUCCESSFULLY_TEMPLATE.format(volume_name=volume_name, dest_pool_name=dest_pool_name),
                        "source_pool": source_pool_name,
                        "dest_pool": dest_pool_name,
                        "updated_vms": updated_vms
                    }
                except Exception as e:
                    return e

            self.run_worker(do_move, name="move_volume_worker", exclusive=True, thread=True)

        self.app.push_screen(MoveVolumeModal(self.conn, source_pool_name, volume_name), on_move)

    @on(Worker.StateChanged)
    def on_move_volume_worker_done(self, event: Worker.StateChanged) -> None:
        """Called when the move volume worker is done."""
        if event.worker.name != "move_volume_worker":
            return

        # We only care about terminal states
        if event.worker.state not in ("SUCCESS", "ERROR"):
            return

        self.app.pop_screen()  # Pop the progress modal
        tree: Tree[dict] = self.query_one("#storage-tree")

        if event.worker.state == "SUCCESS":
            result = event.worker.result
            if isinstance(result, Exception):
                self.app.show_error_message(str(result))
                self._load_storage_pools()
            else:
                self.app.show_success_message(result["message"])
                storage_manager.list_storage_volumes.cache_clear()
                # Refresh the parent node
                parent_node = tree.cursor_node.parent
                tree.cursor_node.remove()
                if parent_node and not parent_node.children:
                    parent_node.add_leaf("No volumes")

                updated_vms = result.get("updated_vms", [])
                if updated_vms:
                    vm_list = ", ".join(updated_vms)
                    self.app.show_success_message(SuccessMessages.UPDATED_VM_CONFIGURATIONS_TEMPLATE.format(vm_list=vm_list))
                self._load_storage_pools(expand_pools=[result["source_pool"], result["dest_pool"]])
        elif event.worker.state == "ERROR":
            self.app.show_error_message(ErrorMessages.MOVE_OPERATION_FAILED_TEMPLATE.format(error=event.worker.error))
            self._load_storage_pools()

    @on(DataTable.RowSelected, "#networks-table")
    def on_network_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.query_one("#view-net-btn").disabled = False
        self.query_one("#delete-net-btn").disabled = False
        self.query_one("#edit-net-btn").disabled = False

        toggle_active_btn = self.query_one("#toggle-net-active-btn")
        toggle_autostart_btn = self.query_one("#toggle-net-autostart-btn")
        toggle_active_btn.disabled = False
        toggle_autostart_btn.disabled = False

        selected_net_name = event.row_key.value
        net_info = next((net for net in self.networks_list if net['name'] == selected_net_name), None)
        if net_info:
            toggle_active_btn.label = "Deactivate" if net_info['active'] else "Activate"
            toggle_autostart_btn.label = "Autostart Off" if net_info['autostart'] else "Autostart On"

    @on(Button.Pressed, "#toggle-net-active-btn")
    def on_toggle_net_active_pressed(self, event: Button.Pressed) -> None:
        table = self.query_one("#networks-table", DataTable)
        if not table.cursor_coordinate:
            return

        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        net_name = row_key.value
        net_info = next((net for net in self.networks_list if net['name'] == net_name), None)

        if net_info:
            try:
                set_network_active(self.conn, net_name, not net_info['active'])
                status = 'inactive' if net_info['active'] else 'active'
                self.app.show_success_message(SuccessMessages.NETWORK_ACTIVATION_CHANGE_TEMPLATE.format(net_name=net_name, status=status))
                list_networks.cache_clear()
                self._load_networks()
            except Exception as e:
                self.app.show_error_message(ErrorMessages.UNEXPECTED_ERROR_OCCURRED_TEMPLATE_XML.format(error=e))

    @on(Button.Pressed, "#toggle-net-autostart-btn")
    def on_toggle_net_autostart_pressed(self, event: Button.Pressed) -> None:
        table = self.query_one("#networks-table", DataTable)
        if not table.cursor_coordinate:
            return

        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        net_name = row_key.value
        net_info = next((net for net in self.networks_list if net['name'] == net_name), None)

        if net_info:
            try:
                set_network_autostart(self.conn, net_name, not net_info['autostart'])
                status = 'off' if net_info['autostart'] else 'on'
                self.app.show_success_message(SuccessMessages.NETWORK_AUTOSTART_CHANGE_TEMPLATE.format(net_name=net_name, status=status))
                list_networks.cache_clear()
                self._load_networks()
            except Exception as e:
                self.app.show_error_message(ErrorMessages.UNEXPECTED_ERROR_OCCURRED_TEMPLATE_XML.format(error=e))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.dismiss(None)
        elif event.button.id == "view-net-btn":
            table = self.query_one("#networks-table", DataTable)
            if not table.cursor_coordinate:
                return

            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            network_name = row_key.value
            try:
                conn = self.conn
                if conn is None:
                    self.app.show_error_message(ErrorMessages.NOT_CONNECTED_TO_LIBVIRT)
                    return
                net = conn.networkLookupByName(network_name)
                network_xml = net.XMLDesc(0)
                self.app.push_screen(NetworkXMLModal(network_name, network_xml))
            except libvirt.libvirtError as e:
                self.app.show_error_message(ErrorMessages.ERROR_GETTING_NETWORK_XML_TEMPLATE.format(error=e))
            except Exception as e:
                self.app.show_error_message(ErrorMessages.UNEXPECTED_ERROR_OCCURRED_TEMPLATE_XML.format(error=e))

        elif event.button.id == "edit-net-btn":
            table = self.query_one("#networks-table", DataTable)
            if not table.cursor_coordinate:
                return

            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            network_name = row_key.value

            network_info = get_network_info(self.conn, network_name)
            if not network_info:
                self.app.show_error_message(ErrorMessages.COULD_NOT_RETRIEVE_NETWORK_INFO_TEMPLATE.format(network_name=network_name))
                return

            def on_create(success: bool):
                if success:
                    list_networks.cache_clear()
                    self._load_networks()
            self.app.push_screen(AddEditNetworkModal(self.conn, network_info=network_info), on_create)

        elif event.button.id == "add-net-btn":
            def on_create(success: bool):
                if success:
                    list_networks.cache_clear()
                    self._load_networks()
            self.app.push_screen(AddEditNetworkModal(self.conn), on_create)

        elif event.button.id == "delete-net-btn":
            table = self.query_one("#networks-table", DataTable)
            if not table.cursor_coordinate:
                return

            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            network_name = row_key.value
            vms_using_network = get_vms_using_network(self.conn, network_name)

            confirm_message = ErrorMessages.DELETE_NETWORK_CONFIRM_TEMPLATE.format(network_name=network_name)
            if vms_using_network:
                vm_list = ", ".join(vms_using_network)
                confirm_message += ErrorMessages.NETWORK_IN_USE_WARNING_TEMPLATE.format(vm_list=vm_list)

            def on_confirm(confirmed: bool) -> None:
                if confirmed:
                    try:
                        delete_network(self.conn, network_name)
                        self.app.show_success_message(SuccessMessages.NETWORK_DELETED_SUCCESSFULLY_TEMPLATE.format(network_name=network_name))
                        list_networks.cache_clear()
                        self._load_networks()
                    except Exception as e:
                        self.app.show_error_message(ErrorMessages.ERROR_DELETING_NETWORK_TEMPLATE.format(network_name=network_name, error=e))

            self.app.push_screen(
                ConfirmationDialog(confirm_message), on_confirm
            )

        elif event.button.id == "help-net-btn":
            self.app.push_screen(HowToNetworkModal())
