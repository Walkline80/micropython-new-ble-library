"""
Copyright © 2024 Walkline Wang (https://walkline.wang)
Gitee: https://gitee.com/walkline/micropython-ble-hid-controller
"""
import json
import binascii
from ble import *
from hid.reportmap.keyboard import REPORT_MAP_DATA


def printf(msg, *args):
	print(f'\033[1;37m[INFO]\033[0m {msg}' % args)


class BLEKeyboard104(object):
	'''标准104键键盘'''
	def __dir__(self):
		return [attr for attr in dir(type(self)) if not attr.startswith('_')]

	def __init__(self, device_name='MP_KB104', report_map: bytes = None, report_count: int = 1):
		self.__ble = bluetooth.BLE()
		self.__ble_values = BLEValues()
		self.__device_name = device_name
		self.__report_map = report_map or REPORT_MAP_DATA
		self.__report_count = report_count
		self.__appearance = 961 # or (0x00f, 0x01)
		self.__conn_handles = set()

		self.__write = self.__ble.gatts_write
		self.__read = self.__ble.gatts_read
		self.__notify = self.__ble.gatts_notify
		self.__indicate = self.__ble.gatts_indicate

		self.__secrets = {}
		self.__load_secrets()

		self.__ble.config(gap_name=device_name)
		self.__ble.config(io=IOCapabilities.NO_INPUT_OUTPUT)
		self.__ble.config(bond=True, le_secure=True, mitm=True)

		self.__ble.irq(self.__irq_callback)

		self.__ble.active(False)
		printf('Activating BLE...')
		self.__ble.active(True)
		printf(f'BLE Activated [{BLETools.decode_mac(self.__ble.config('mac')[1])}]')

		self.__ble.config(addr_mode=2, mtu=256)

		generic_profile = GenericProfile()
		hid_profile = HIDProfile(report_count)

		# 先由 __handle_reports 接收 reports 和 report_references 的 handle
		# 然后 __handle_report_references 从 __handle_reports 中提取所需值
		self.__handle_reports = [None for _ in range(hid_profile.report_count * 2)]
		self.__handle_report_references = []

		self.__register_services(generic_profile.get_services() + hid_profile.get_services())
		self.__setup_hid_values()

		adv_payload = BLETools.generate_advertising_payload(
			generic_profile.get_services_uuid(),
			appearance=self.__ble_values.generic_access.__appearance,
			name=self.__device_name
		)

		resp_payload = BLETools.generate_advertising_payload(
			hid_profile.get_services_uuid()
		)

		assert (len(adv_payload)  <= MAX_PAYLOAD_LENGTH) and\
			   (len(resp_payload) <= MAX_PAYLOAD_LENGTH),\
			   f'Advertising payload too long, more than {MAX_PAYLOAD_LENGTH} bytes'

		self.__advertise(adv_payload, resp_payload)

	def __register_services(self, services: list):
		(
			(
				self.__handle_device_name,
				self.__handle_appearance,
				self.__handle_ppcp, # peripheral_preferred_connection_parameters
			),
			(
				_, # self.__handle_service_changed,
			),
			(
				self.__handle_manufacturer_name,
				self.__handle_model_number,
				self.__handle_serial_number,
				self.__handle_hardware_revision,
				self.__handle_firmware_revision,
				self.__handle_software_revision,
				self.__handle_pnp_id,
			),
			(
				self.__handle_battery_level,
			),
			(
				self.__handle_hid_information,
				_, # self.__handle_boot_keyboard_input,
				_, # self.__handle_boot_keyboard_output,
				_, # self.__handle_boot_mouse_input_report,
				self.__handle_report_map,
				_, #self.__handle_hid_control_point,
				self.__handle_protocol_mode,

				*self.__handle_reports,
			),
		) = self.__ble.gatts_register_services(services)

		self.__handle_report_references.extend(self.__handle_reports[1::2])
		self.__handle_reports = self.__handle_reports[::2]

		if False:
			print('\tdevice_name', self.__handle_device_name)
			print('\tappearance', self.__handle_appearance)
			print('\tppcp', self.__handle_ppcp)
			print('\tmanufacturer_name', self.__handle_manufacturer_name)
			print('\tmodel_number', self.__handle_model_number)
			print('\tserial_number', self.__handle_serial_number)
			print('\thardware_revision', self.__handle_hardware_revision)
			print('\tfirmware_revision', self.__handle_firmware_revision)
			print('\tsoftware_revision', self.__handle_software_revision)
			print('\tpnp_id', self.__handle_pnp_id)
			print('\tbattery_level', self.__handle_battery_level)
			print('\thid_information',self.__handle_hid_information)
			print('\treport_map',self.__handle_report_map)
			print('\tprotocol_mode',self.__handle_protocol_mode)

			self.__ble_values.human_interface_device.report_count = self.__report_count

			for index in range(self.__report_count):
				print(f'\treport{index}', self.__handle_reports[index])
				print('\treport_reference', self.__handle_report_references[index],
					self.__ble_values.human_interface_device.report_reference[index])

		printf('Services Registered')

	def __advertise(self, adv_payload: bytes = None, resp_payload: bytes = None, interval_us: int = 100000):
		self.__ble.gap_advertise(None)
		self.__ble.gap_advertise(interval_us, adv_data=adv_payload, resp_data=resp_payload)

		printf('Advertising Payload...')

	def __irq_callback(self, event, data):
		if event == IRQ.CENTRAL_CONNECT:
			conn_handle, _, addr, = data # _: addr_type

			self.__conn_handles.add(conn_handle)
			self.__ble.gap_advertise(None)

			printf(f'[{BLETools.decode_mac(addr)}] Connected [Handle: {conn_handle}]')
		elif event == IRQ.CENTRAL_DISCONNECT:
			conn_handle, _, addr, = data # _: addr_type

			if conn_handle in self.__conn_handles:
				self.__conn_handles.remove(conn_handle)

			printf(f'[{BLETools.decode_mac(addr)}] Disconnected [Handle: {conn_handle}]')

			self.__advertise()
		elif event == IRQ.GATTC_INDICATE:
			conn_handle, value_handle, data = data

			printf(f'GATTC Indicate [Handle: {conn_handle}, Value_Handle: {value_handle}, Data: {bytes(data)}]')
		elif event == IRQ.GATTS_READ_REQUEST:
			conn_handle, attr_handle = data

			if conn_handle != 0xffff:
				printf(f'GATTS Read Request [Handle: {conn_handle}, Attr_Handle: {attr_handle}]')

			return GATTSErrorCode.NO_ERROR
		elif event == IRQ.CONNECTION_UPDATE:
			conn_handle, interval, latency, supervision_timeout, status = data

			printf(f'Connection Update [Handle: {conn_handle}, Interval: {interval}, Latency: {latency}, Supervision_Timeout: {supervision_timeout}, Status: {status}]')
		elif event == IRQ.ENCRYPTION_UPDATE:
			conn_handle, encrypted, authenticated, bonded, key_size = data

			printf(f'Encryption Update [Handle: {conn_handle}, Encrypted: {bool(encrypted)}, Authenticated: {bool(authenticated)}, Bonded: {bool(bonded)}, Key_Size: {key_size}]')
		elif event == IRQ.PASSKEY_ACTION:
			conn_handle, action, passkey = data

			printf(f'Passkey Action [Handle: {conn_handle}, Action: {action}, Passkey: {passkey}]')

			if action == PasskeyAction.NUMERIC_COMPARISON:
				printf('Prompting for passkey')

				accept = int(input('Accept? (0/1): '))
				self.__ble.gap_passkey(conn_handle, action, accept)
			elif action == PasskeyAction.DISPLAY:
				printf('Displaying 123456')

				self.__ble.gap_passkey(conn_handle, action, 123456)
			elif action == PasskeyAction.INPUT:
				printf('Prompting for passkey')

				passkey = int(input('passkey? '))
				self.__ble.gap_passkey(conn_handle, action, passkey)
			else:
				printf('Unknown Passkey Action')
		elif event == IRQ.GATTS_INDICATE_DONE:
			conn_handle, value_handle, status = data

			printf(f'GATTS Indicate Done [Handle: {conn_handle}, Value_Handle: {value_handle}, Status: {status}]')
		elif event == IRQ.SET_SECRET:
			result = True
			sec_type, key, value = data
			key = sec_type, bytes(key)
			value = bytes(value) if value else None

			if value is None:
				if key in self.__secrets:
					del self.__secrets[key]
				else:
					result = False
			else:
				self.__secrets[key] = value

			if result:
				self.__save_secrets()

			return result
		elif event == IRQ.GET_SECRET:
			sec_type, index, key = data

			if key is None:
				i = 0
				for (t, _key), value in self.__secrets.items():
					if t == sec_type:
						if i == index:
							return value
						i += 1
				return None
			else:
				key = sec_type, bytes(key)
				return self.__secrets.get(key, None)
		elif event == IRQ.MTU_EXCHANGED:
			conn_handle, mtu = data

			printf(f'MTU Exchanged [Handle: {conn_handle}, MTU: {mtu}]')
		else:
			printf(f'Uncaught IRQ Event: {event}, Data: {data}')

	def __load_secrets(self):
		try:
			with open('secrets.json', 'r') as f:
				entries = json.load(f)
				for sec_type, key, value in entries:
					self.__secrets[sec_type, binascii.a2b_base64(key)] = binascii.a2b_base64(value)
		except:
			printf('No secrets available')

		printf('Secrets Loaded')

	def __save_secrets(self):
		try:
			with open('secrets.json', 'w') as f:
				json_secrets = [
					(sec_type, binascii.b2a_base64(key), binascii.b2a_base64(value))
					for (sec_type, key), value in self.__secrets.items()
				]
				json.dump(json_secrets, f)
		except:
			printf('Failed to save secrets')

	def __setup_hid_values(self):
		# GenericAccess values
		self.__ble_values.generic_access.device_name = self.__device_name
		self.__ble_values.generic_access.appearance  = self.__appearance
		# self.__ble_values.generic_access.ppcp        = [40, 80, 10, 300]

		self.__write(self.__handle_device_name, self.__ble_values.generic_access.device_name)
		self.__write(self.__handle_appearance,  self.__ble_values.generic_access.appearance)
		self.__write(self.__handle_ppcp,        self.__ble_values.generic_access.ppcp)


		# DeviceInformation values
		# self.__ble_values.device_information.manufacturer_name = 'Walkline Wang'
		# self.__ble_values.device_information.model_number      = 'MP_KB'
		# self.__ble_values.device_information.serial_number     = '4e897424-061d-4dd9-a798-454f79b37245'
		# self.__ble_values.device_information.firmware_revision = 'v0.1'
		# self.__ble_values.device_information.hardware_revision = 'v0.2'
		# self.__ble_values.device_information.software_revision = 'v0.3'
		# self.__ble_values.device_information.pnp_id            = 0x02E5 # 0x02E5: Espressif, 0x0006: Microsoft

		self.__write(self.__handle_manufacturer_name, self.__ble_values.device_information.manufacturer_name)
		self.__write(self.__handle_model_number,      self.__ble_values.device_information.model_number)
		self.__write(self.__handle_serial_number,     self.__ble_values.device_information.serial_number)
		self.__write(self.__handle_hardware_revision, self.__ble_values.device_information.hardware_revision)
		self.__write(self.__handle_firmware_revision, self.__ble_values.device_information.firmware_revision)
		self.__write(self.__handle_software_revision, self.__ble_values.device_information.software_revision)
		self.__write(self.__handle_pnp_id,            self.__ble_values.device_information.pnp_id)


		# BatteryService value
		self.__ble_values.battery_service.battery_level = 100

		self.__write(self.__handle_battery_level, self.__ble_values.battery_service.battery_level)


		# HumanInterfaceDevice values
		# self.__ble_values.human_interface_device.hid_information = [0x0111, 0x00, 0b00]
		# self.__ble_values.human_interface_device.protocol_mode   = 1
		self.__ble_values.human_interface_device.report_count = self.__report_count

		self.__write(self.__handle_hid_information, self.__ble_values.human_interface_device.hid_information)
		self.__write(self.__handle_report_map,      bytes(self.__report_map))
		self.__write(self.__handle_protocol_mode,   self.__ble_values.human_interface_device.protocol_mode)

		for index in range(self.__report_count):
			self.__write(self.__handle_report_references[index], self.__ble_values.human_interface_device.report_reference[index])

	def update_battery_level(self, value: int = None):
		import random

		random.seed(random.randint(-2**16, 2**16))

		self.__ble_values.battery_service.battery_level = value or int(random.randint(1, 80))
		self.__write(self.__handle_battery_level, self.__ble_values.battery_service.battery_level)

		for conn_handle in self.__conn_handles:
			self.__notify(conn_handle, self.__handle_battery_level)

	def send_kb_key_down(self, key_data, report_id: int = 0):
		if self.__conn_handles is None:
			return

		self.__write(self.__handle_reports[report_id], key_data)

		for conn_handle in self.__conn_handles:
			self.__notify(conn_handle, self.__handle_reports[report_id])

	def send_kb_key_up(self, key_data, report_id: int = 0):
		if self.__conn_handles is None:
			return

		self.__write(self.__handle_reports[report_id], key_data)

		for conn_handle in self.__conn_handles:
			self.__notify(conn_handle, self.__handle_reports[report_id])

	@property
	def report_count(self):
		return self.__report_count
