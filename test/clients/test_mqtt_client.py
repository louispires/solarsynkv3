import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

import src.clients.mqtt_client as mqtt_client_module
from src.clients.mqtt_client import MqttClient, _discovery_fields


def _configuration(**overrides):
    configuration = MagicMock()
    configuration.mqtt_enabled.return_value = overrides.get('mqtt_enabled', True)
    configuration.mqtt_host.return_value = overrides.get('mqtt_host', 'broker-host')
    configuration.mqtt_port.return_value = overrides.get('mqtt_port', 1883)
    configuration.mqtt_username.return_value = overrides.get('mqtt_username', 'user')
    configuration.mqtt_password.return_value = overrides.get('mqtt_password', 'pass')
    configuration.mqtt_discovery_prefix.return_value = overrides.get('mqtt_discovery_prefix', 'homeassistant')
    configuration.mqtt_base_topic.return_value = overrides.get('mqtt_base_topic', 'solarsynkv3')
    return configuration


class TestDiscoveryFields(TestCase):
    def test_kwh_is_total_increasing_energy(self):
        self.assertEqual(('energy', 'kWh', 'total_increasing'), _discovery_fields('kWh', 'energy'))

    def test_voltage_is_measurement(self):
        self.assertEqual(('voltage', 'V', 'measurement'), _discovery_fields('V', 'voltage'))

    def test_mismatched_device_class_is_dropped(self):
        # GetDCACTemp sends device_class "power" with a °C unit; the invalid class is dropped.
        self.assertEqual((None, '°C', 'measurement'), _discovery_fields('°C', 'power'))

    def test_plain_sensor_has_no_class_unit_or_state_class(self):
        self.assertEqual((None, None, None), _discovery_fields('', ''))

    def test_unknown_device_class_is_dropped_but_unit_kept(self):
        self.assertEqual((None, 'A', 'measurement'), _discovery_fields('A', 'not_a_class'))


class TestMqttClient(TestCase):
    def setUp(self):
        self.paho_client = MagicMock()
        self.paho_module = MagicMock()
        self.paho_module.Client.return_value = self.paho_client

    def _build(self, **overrides):
        with patch.object(mqtt_client_module, 'Configuration', return_value=_configuration(**overrides)):
            client = MqttClient()
        return client

    def test_connect_returns_false_when_disabled(self):
        client = self._build(mqtt_enabled=False)
        with patch.object(mqtt_client_module, 'mqtt', self.paho_module):
            self.assertFalse(client.connect())
        self.paho_module.Client.assert_not_called()

    def test_connect_returns_false_when_no_host(self):
        client = self._build(mqtt_host='')
        with patch.object(mqtt_client_module, 'mqtt', self.paho_module):
            self.assertFalse(client.connect())

    def test_connect_returns_false_when_paho_missing(self):
        client = self._build()
        with patch.object(mqtt_client_module, 'mqtt', None):
            self.assertFalse(client.connect())

    def test_connect_publishes_online_and_sets_will(self):
        client = self._build()
        with patch.object(mqtt_client_module, 'mqtt', self.paho_module):
            self.assertTrue(client.connect())

        self.paho_client.username_pw_set.assert_called_once_with('user', 'pass')
        self.paho_client.will_set.assert_called_once_with('solarsynkv3/status', 'offline', qos=1, retain=True)
        self.paho_client.connect.assert_called_once_with('broker-host', 1883, keepalive=60)
        self.paho_client.loop_start.assert_called_once()
        self.paho_client.publish.assert_any_call('solarsynkv3/status', 'online', qos=1, retain=True)

    def test_connect_is_idempotent(self):
        client = self._build()
        with patch.object(mqtt_client_module, 'mqtt', self.paho_module):
            self.assertTrue(client.connect())
            self.assertTrue(client.connect())
        self.paho_module.Client.assert_called_once()

    def test_publish_sensor_publishes_config_and_state(self):
        client = self._build()
        with patch.object(mqtt_client_module, 'mqtt', self.paho_module):
            client.connect()
            self.paho_client.publish.reset_mock()
            result = client.publish_sensor('VSN-E47W23641127-01', 'battery_soc', 'Battery SOC', '%', 'battery', '56.0')

        self.assertTrue(result)

        config_call = self.paho_client.publish.call_args_list[0]
        state_call = self.paho_client.publish.call_args_list[1]

        self.assertEqual(
            'homeassistant/sensor/solarsynkv3_vsn_e47w23641127_01/battery_soc/config',
            config_call.args[0]
        )
        config = json.loads(config_call.args[1])
        self.assertEqual('solarsynkv3_vsn_e47w23641127_01_battery_soc', config['unique_id'])
        self.assertEqual('solarsynkv3_vsn_e47w23641127_01_battery_soc', config['object_id'])
        self.assertEqual('solarsynkv3/vsn_e47w23641127_01/battery_soc', config['state_topic'])
        self.assertEqual('battery', config['device_class'])
        self.assertEqual('%', config['unit_of_measurement'])
        self.assertEqual('measurement', config['state_class'])
        self.assertEqual(['solarsynkv3_vsn_e47w23641127_01'], config['device']['identifiers'])
        self.assertEqual('SolarSynk VSN-E47W23641127-01', config['device']['name'])
        self.assertTrue(config_call.kwargs['retain'])

        self.assertEqual('solarsynkv3/vsn_e47w23641127_01/battery_soc', state_call.args[0])
        self.assertEqual('56.0', state_call.args[1])

    def test_publish_sensor_returns_false_when_not_connected(self):
        client = self._build()
        self.assertFalse(client.publish_sensor('SER', 'sn', 'SN', '', '', 'x'))

    def test_disconnect_publishes_offline_and_stops(self):
        client = self._build()
        with patch.object(mqtt_client_module, 'mqtt', self.paho_module):
            client.connect()
            self.paho_client.publish.reset_mock()
            client.disconnect()

        self.paho_client.publish.assert_any_call('solarsynkv3/status', 'offline', qos=1, retain=True)
        self.paho_client.loop_stop.assert_called_once()
        self.paho_client.disconnect.assert_called_once()
