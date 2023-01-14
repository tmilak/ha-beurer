from typing import Tuple
from bleak import BleakClient, BleakScanner
import traceback
import asyncio

from .const import LOGGER

WRITE_CHARACTERISTIC_UUIDS = ["8b00ace7-eb0b-49b0-bbe9-9aee0a26e1a3"]
READ_CHARACTERISTIC_UUIDS  = ["0734594A-A8E7-4B1A-A6B1-CD5243059A57"]

async def discover():
    """Discover Bluetooth LE devices."""
    devices = await BleakScanner.discover()
    LOGGER.debug("Discovered devices: %s", [{"address": device.address, "name": device.name} for device in devices])
    return [device for device in devices if device.name.lower().startswith("TL100")]

def create_status_callback(future: asyncio.Future):
    def callback(sender: int, data: bytearray):
        if not future.done():
            future.set_result(data)
    return callback

class BeurerInstance:
    def __init__(self, mac: str) -> None:
        self._mac = mac
        self._device = BleakClient(self._mac)
        self._is_on = None
        self._rgb_color = None
        self._brightness = None
        self._write_uuid = None
        self._read_uuid = None

    async def _write(self, data: bytearray):
        LOGGER.debug(''.join(format(x, ' 03x') for x in data))
        await self._device.write_gatt_char(self._write_uuid, data)

    @property
    def mac(self):
        return self._mac

    @property
    def is_on(self):
        return self._is_on

    @property
    def rgb_color(self):
        return self._rgb_color

    @property
    def white_brightness(self):
        return self._brightness

    def makeChecksum(b: int, bArr: List[int]) -> int:
      for b2 in bArr:
          b = b ^ b2
      return b

    async def set_color(self, rgb: Tuple[int, int, int]):
        r, g, b = rgb
        checksum = makeChecksum(6,[0x32,r,g,b])
        await self._write([0xFE,0xEF,0x0A,0x0B,0xAB,0xAA,0x06,0x32,r,g,b,checksum,0x55,0x0D,0x0A])

    async def set_white(self, intensity: int):
        checksum = makeChecksum(5,[0x31,0x01,intensity])
        await self._write([0xFE,0xEF,0x0A,0x0B,0xAB,0xAA,0x05,0x31,0x01,intensity,checksum,0x55,0x0D,0x0A])

    async def turn_on(self):
        checksum = makeChecksum(4,[0x37,0x01])
        await self._write([0xFE,0xEF,0x0A,0x0B,0xAB,0xAA,0x04,0x37,0x01,checksum,0x55,0x0D,0x0A])

    async def turn_off(self):
        checksum = makeChecksum(4,[0x35,0x01])
        await self._write([0xFE,0xEF,0x0A,0x0B,0xAB,0xAA,0x04,0x35,0x01,checksum,0x55,0x0D,0x0A])

    async def update(self):
        try:
            if not self._device.is_connected:
                await self._device.connect(timeout=20)
                await asyncio.sleep(1)

                for char in self._device.services.characteristics.values():
                    if char.uuid in WRITE_CHARACTERISTIC_UUIDS:
                        self._write_uuid = char.uuid
                    if char.uuid in READ_CHARACTERISTIC_UUIDS:
                        self._read_uuid = char.uuid

                if not self._read_uuid or not self._write_uuid:
                    LOGGER.error("No supported read/write UUIDs found")
                    return

                LOGGER.info(f"Read UUID: {self._read_uuid}, Write UUID: {self._write_uuid}")

            await asyncio.sleep(2)

            future = asyncio.get_event_loop().create_future()
            await self._device.start_notify(self._read_uuid, create_status_callback(future))

            #Trigger notification with current values
            await self._write(bytearray([0xFE,0xEF,0x0A,0x09,0xAB,0xAA,0x04,0x30,0x02,0x36,0x55,0x0D,0x0A]))

            await asyncio.wait_for(future, 5.0)
            await self._device.stop_notify(self._read_uuid)

            res = future.result()
            reply_version = res[9]
            #Short version with only _brightness
            if reply_version == 1:
                self._is_on = True if res[10] == 1 else None
                self._brightness = res[11] if res[11] > 0 else None
                self._rgb_color = None
            #Long version with color information
            else if reply_version == 2:
                self._is_on = True if res[10] == 1 else None
                self._brightness = res[11] if res[11] > 0 else None
                self._rgb_color = (res[14], res[15], res[16])
            #Unknown reply
            else
                self._is_on = None
                self._rgb_color = None
                self._brightness = None
            LOGGER.debug(''.join(format(x, ' 03x') for x in res))

        except (Exception) as error:
            self._is_on = None
            LOGGER.error("Error getting status: %s", error)
            track = traceback.format_exc()
            LOGGER.debug(track)

    async def disconnect(self):
        if self._device.is_connected:
            await self._device.disconnect()