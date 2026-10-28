import {createApp} from 'vue';
import App from './src/App.vue';

frappe.provide('internal_ledger');
internal_ledger.mount_workbench = element => createApp(App).mount(element);
