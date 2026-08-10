import test from 'node:test'
import assert from 'node:assert/strict'

import { mergeProtocolConnectionConfig } from '../src/pages/Protocol/formUtils.ts'

test('以太网连接后的实际端点和状态以后端 socket 结果为准', () => {
  const merged = mergeProtocolConnectionConfig({
    protocol: 'ethernet',
    responseConfigInput: {
      transport_protocol: 'TCP Client',
      target_ip: '127.0.0.1',
      target_port: 9000,
      local_ip: '127.0.0.1',
      local_port: 53124,
      remote_ip: '127.0.0.1',
      remote_port: 9000,
      channel_state: 'connected',
      peer_connected: true,
    },
    requestedConfigInput: {
      protocol: 'TCP Client',
      target_ip: '127.0.0.1',
      target_port: 9000,
      local_port: 8080,
    },
  })

  assert.equal(merged.local_port, 53124)
  assert.equal(merged.remote_port, 9000)
  assert.equal(merged.channel_state, 'connected')
  assert.equal(merged.peer_connected, true)
})

test('经典 CAN 设备身份字段以后端扫描结果为准，但保留用户波特率等配置', () => {
  const merged = mergeProtocolConnectionConfig({
    protocol: 'can',
    responseConfigInput: {
      backend_key: 'zqwl_ucan_cdc',
      adapter_key: 'zqwl_ucan_cdc:COM8:953DAD95240A',
      adapter_name: 'ZQWL USB-CAN',
      adapter_device: 'COM8',
      com_port: 'COM8',
      physical_channel: 'CAN0',
      detected_devices: [{ adapter_device: 'COM8' }],
      vid: 0x3562,
      pid: 0x0103,
    },
    requestedConfigInput: {
      backend_key: 'zqwl_ucan_cdc',
      adapter_key: 'zqwl_ucan_cdc:COM7:OLD',
      adapter_device: 'COM7',
      com_port: 'COM7',
      physical_channel: 'CAN1',
      detected_devices: [{ adapter_device: 'COM7' }],
      baud_rate: '250kbps',
      data_length: 8,
      expected_rx_id: '0x123',
    },
  })

  assert.equal(merged.adapter_key, 'zqwl_ucan_cdc:COM8:953DAD95240A')
  assert.equal(merged.adapter_device, 'COM8')
  assert.equal(merged.com_port, 'COM8')
  assert.equal(merged.physical_channel, 'CAN0')
  assert.deepEqual(merged.detected_devices, [{ adapter_device: 'COM8' }])
  assert.equal(merged.baud_rate, '250kbps')
  assert.equal(merged.data_length, 8)
  assert.equal(merged.expected_rx_id, undefined)
})

test('CAN FD 旧 adapter_key 不能覆盖后端最新扫描结果，detected_devices 始终使用后端返回', () => {
  const merged = mergeProtocolConnectionConfig({
    protocol: 'canfd',
    responseConfigInput: {
      backend_key: 'usbcanfd_200u',
      adapter_key: 'usbcanfd_200u:SERIAL-A',
      adapter_device: 'USB\\VID_3068&PID_0009\\SERIAL-A',
      sdk_device_index: 4,
      detected_devices: [{ adapter_key: 'usbcanfd_200u:SERIAL-A' }],
      brs: true,
      data_baud_rate: '2Mbps',
    },
    requestedConfigInput: {
      backend_key: 'zqwl_ucan_cdc',
      adapter_key: 'old:adapter',
      adapter_device: 'COM7',
      detected_devices: [{ adapter_key: 'old:adapter' }],
      bitrate: '500kbps',
      brs: false,
      data_baud_rate: '4Mbps',
    },
  })

  assert.equal(merged.backend_key, 'usbcanfd_200u')
  assert.equal(merged.adapter_key, 'usbcanfd_200u:SERIAL-A')
  assert.equal(merged.adapter_device, 'USB\\VID_3068&PID_0009\\SERIAL-A')
  assert.equal(merged.sdk_device_index, 4)
  assert.deepEqual(merged.detected_devices, [{ adapter_key: 'usbcanfd_200u:SERIAL-A' }])
  assert.equal(merged.bitrate, '500kbps')
  assert.equal(merged.brs, false)
  assert.equal(merged.data_baud_rate, '4Mbps')
})

test('CAN 与 CAN FD 扫描结果不会在合并时互相污染', () => {
  const classic = mergeProtocolConnectionConfig({
    protocol: 'can',
    responseConfigInput: {
      backend_key: 'zqwl_ucan_cdc',
      adapter_key: 'zqwl_ucan_cdc:COM8:953DAD95240A',
      detected_devices: [{ backend_key: 'zqwl_ucan_cdc', adapter_device: 'COM8' }],
    },
    requestedConfigInput: {
      backend_key: 'usbcanfd_200u',
      detected_devices: [{ backend_key: 'usbcanfd_200u', adapter_device: 'USB-A' }],
      bitrate: '500kbps',
    },
  })

  const canfd = mergeProtocolConnectionConfig({
    protocol: 'canfd',
    responseConfigInput: {
      backend_key: 'usbcanfd_200u',
      adapter_key: 'usbcanfd_200u:SERIAL-A',
      detected_devices: [{ backend_key: 'usbcanfd_200u', adapter_device: 'USB-A' }],
    },
    requestedConfigInput: {
      backend_key: 'zqwl_ucan_cdc',
      detected_devices: [{ backend_key: 'zqwl_ucan_cdc', adapter_device: 'COM8' }],
      bitrate: '1Mbps',
    },
  })

  assert.equal(classic.backend_key, 'zqwl_ucan_cdc')
  assert.deepEqual(classic.detected_devices, [{ backend_key: 'zqwl_ucan_cdc', adapter_device: 'COM8' }])
  assert.equal(canfd.backend_key, 'usbcanfd_200u')
  assert.deepEqual(canfd.detected_devices, [{ backend_key: 'usbcanfd_200u', adapter_device: 'USB-A' }])
})

test('CAN FD 合并配置时保留终端电阻和 ISO 标准配置', () => {
  const merged = mergeProtocolConnectionConfig({
    protocol: 'canfd',
    responseConfigInput: {
      backend_key: 'usbcanfd_200u',
      adapter_key: 'usbcanfd_200u:SERIAL-A',
      detected_devices: [{ adapter_key: 'usbcanfd_200u:SERIAL-A' }],
      termination_enabled: true,
      canfd_non_iso: false,
    },
    requestedConfigInput: {
      termination_enabled: true,
      canfd_non_iso: false,
      brs: false,
    },
  })

  assert.equal(merged.termination_enabled, true)
  assert.equal(merged.canfd_non_iso, false)
  assert.equal(merged.brs, false)
})

test('经典 CAN 合并配置时保留内部终端电阻开关', () => {
  const merged = mergeProtocolConnectionConfig({
    protocol: 'can',
    responseConfigInput: {
      backend_key: 'usbcanfd_200u',
      adapter_key: 'usbcanfd_200u:SERIAL-A',
      detected_devices: [{ adapter_key: 'usbcanfd_200u:SERIAL-A' }],
      termination_enabled: false,
    },
    requestedConfigInput: {
      termination_enabled: true,
    },
  })

  assert.equal(merged.termination_enabled, true)
})
