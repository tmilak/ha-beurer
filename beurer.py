from typing import Tuple
from bleak import BleakClient, BleakScanner, BLEDevice, BleakGATTCharacteristic, BleakError
import traceback
import asyncio

from homeassistant.components.light import (COLOR_MODE_RGB, COLOR_MODE_WHITE)

from .const import LOGGER

WRITE_CHARACTERISTIC_UUIDS = ["8b00ace7-eb0b-49b0-bbe9-9aee0a26e1a3"]
READ_CHARACTERISTIC_UUIDS  = ["0734594a-a8e7-4b1a-a6b1-cd5243059a57"]

async def discover():
    """Discover Bluetooth LE devices."""
    devices = await BleakScanner.discover()
    LOGGER.debug("Discovered devices: %s", [{"address": device.address, "name": device.name} for device in devices])
    return [device for device in devices if device.name.lower().startswith("tl100")]

def create_status_callback(future: asyncio.Future):
    def callback(sender: int, data: bytearray):
        if not future.done():
            future.set_result(data)
    return callback

async def get_device(mac: str) -> BLEDevice:
    devices = await BleakScanner.discover()
    LOGGER.debug(f"Discovered devices: {devices}")
    return next((device for device in devices if device.address.lower()==mac.lower()),None)

class BeurerInstance:
    def __init__(self, device: BLEDevice) -> None:
        self._mac = device.address
        #device = get_device(self._mac)
        if device == None:
            LOGGER.error(f"Was not able to find device with mac {self._mac}")
        self._device = BleakClient(device)
        self._is_on = None
        self._light_on = None
        self._color_on = None
        self._rgb_color = None
        self._brightness = None
        self._color_brightness = None
        self._effect = None
        self._write_uuid = None
        self._read_uuid = None
        self._mode = None
        self._supported_effects = ["Off", "Random", "Rainbow", "Rainbow Slow", "Fusion", "Pulse", "Wave", "Chill", "Action", "Forest", "Summer"]
        self.connect()

    async def _write(self, data: bytearray):
        LOGGER.debug("Sending in write: " + ''.join(format(x, ' 03x') for x in data))
        if not self._device.is_connected:
            await self._device.connect(timeout=20)
        try:
            await self._device.write_gatt_char(self._write_uuid, data)
        except (BleakError) as error:
            self._is_on = None
            self._light_on = None
            self._color_on = None
            track = traceback.format_exc()
            LOGGER.debug(track)
            LOGGER.warn(f"Error while trying to write to device: {error}")

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
    def color_brightness(self):
        return self._color_brightness

    @property
    def white_brightness(self):
        return self._brightness

    @property
    def effect(self):
        return self._effect

    @property
    def color_mode(self):
        return self._mode

    @property
    def supported_effects(self):
        return self._supported_effects

    def find_effect_position(self, effect) -> int:
        try:
            return self._supported_effects.index(effect)
        except ValueError:
            return None



    def makeChecksum(self, b: int, bArr: list[int]) -> int:
        for b2 in bArr:
            b = b ^ b2
        return b

    async def sendPacket(self, message: list[int]):
          #LOGGER.debug(f"Sending packet with length {message.length}: {message}")
        if not self._device.is_connected:
            self.connect()
        length=len(message)
        checksum = self.makeChecksum(length+2,message) #Plus two bytes
        packet=[0xFE,0xEF,0x0A,length+7,0xAB,0xAA,length+2]+message+[checksum,0x55,0x0D,0x0A]
        print("Sending message"+''.join(format(x, ' 03x') for x in packet))
        await self._write(packet)

    async def set_color(self, rgb: Tuple[int, int, int], brightness: int):
        r, g, b = rgb
        LOGGER.debug(f"Setting to color: %s, %s, %s - brightness %s", r, g, b, brightness)
        self._mode = COLOR_MODE_RGB
        self._rgb_color = (r,g,b)
        if not self._color_on:
            await self.turn_on()
        #Send color
        await self.sendPacket([0x32,r,g,b])
        await asyncio.sleep(0.2)
        #send _brightness
        await self.sendPacket([0x31,0x02,int(brightness/255*100)])

    async def set_white(self, intensity: int):
        LOGGER.debug(f"Setting white to intensity: %s", intensity)
        self._brightness = intensity
        self._mode = COLOR_MODE_WHITE
        if not self._light_on:
            await self.turn_on()
        await self.sendPacket([0x31,0x01,int(intensity/255*100)])
        await asyncio.sleep(0.2)
        self.set_effect("Off")

    async def set_effect(self, effect: str):
        LOGGER.debug(f"Setting effect {effect}")
        self._mode = COLOR_MODE_RGB
        if not self._color_on:
            await self.turn_on()
        await self.sendPacket([0x34,self.find_effect_position(effect)])

    async def turn_on(self):
        LOGGER.debug("Turning on")
        #WHITE mode
        if self._mode == COLOR_MODE_WHITE:
            await self.sendPacket([0x37,0x01])
        #COLOR mode
        else:
            await self.sendPacket([0x37,0x02])
        await asyncio.sleep(0.2)

    async def turn_off(self):
        LOGGER.debug("Turning off")
        #turn off white
        await self.sendPacket([0x35,0x01])
        #turn off color
        await self.sendPacket([0x35,0x02])

    async def triggerStatus(self):
        #Trigger notification with current values
        await self.sendPacket([0x30,0x01])
        await asyncio.sleep(0.2)
        await self.sendPacket([0x30,0x02])
        LOGGER.info(f"Triggered update")

    #We receive status version 1 then version 2.
    # So changes to the light status shall only be done in version 2 handler
    async def notification_handler(self, characteristic: BleakGATTCharacteristic, res: bytearray):
        """Simple notification handler which prints the data received."""
        #LOGGER.info("Received notification %s: %r", characteristic.description, res)
        LOGGER.debug("Received notification: " + ''.join(format(x, ' 03x') for x in res))
        if len(res) < 9:
            return
        reply_version = res[8]
        LOGGER.debug(f"Reply version is {reply_version}")
        #Short version with only _brightness
        if reply_version == 1:
            self._brightness = int(res[10]*255/100) if res[10] > 0 else None
            self._light_on = True if res[9] == 1 else False
            if res[9] == 1:
                self._mode = COLOR_MODE_WHITE
            #self._is_on = self._light_on or self._color_on
            #LOGGER.debug(f"res: {res[9]}, light_on {self._light_on}, color_on {self._color_on}")
            LOGGER.debug(f"Short version, on: {self._is_on}, brightness: {self._brightness}")
        #Long version with color information
        elif reply_version == 2:
                self._color_on = True if res[9] == 1 else False
                if res[9] == 1:
                    self._mode = COLOR_MODE_RGB
                self._color_brightness = int(res[10]*255/100) if res[10] > 0 else None
                self._rgb_color = (res[13], res[14], res[15])
                self._effect = self._supported_effects[res[16]]
                self._is_on = self._light_on or self._color_on
                LOGGER.debug(f"Long version, on: {self._is_on}, brightness: {self._color_brightness}, rgb color: {self._rgb_color}, effect: {self._effect}")
            #Unknown reply
        elif reply_version == 255:
                self._is_on = False
                self._light_on = False
                self._color_on = False
                self._mode = None
                LOGGER.debug(f"Unkown version 255")
        elif reply_version == 0:
            LOGGER.debug(f"Unkown version 0")
            return
        else:
            LOGGER.debug(f"Received unknown notification")
            return
            #self._is_on = None
            #self._rgb_color = None
            #self._brightness = None
            #self._color_brightness = None
            #self._mode = None

    async def connect(self):
        try:
            if not self._device.is_connected:
                await self._device.connect(timeout=20)
                await asyncio.sleep(0.1)

                for char in self._device.services.characteristics.values():
                    if char.uuid in WRITE_CHARACTERISTIC_UUIDS:
                        self._write_uuid = char.uuid
                    if char.uuid in READ_CHARACTERISTIC_UUIDS:
                        self._read_uuid = char.uuid

                if not self._read_uuid or not self._write_uuid:
                    LOGGER.error("No supported read/write UUIDs found")
                    return

                LOGGER.info(f"Read UUID: {self._read_uuid}, Write UUID: {self._write_uuid}")

            await asyncio.sleep(0.1)
            LOGGER.info(f"Starting notifications")

            await self._device.start_notify(self._read_uuid, self.notification_handler)

            await self.triggerStatus()
            await asyncio.sleep(0.1)
        except (Exception) as error:
            self._is_on = None
            self._light_on = False
            self._color_on = False
            track = traceback.format_exc()
            LOGGER.debug(track)
            LOGGER.error(f"Error connecting: {error}")

    async def update(self):
        try:
            if not self._device.is_connected:
                await self.connect()
            await self._device.start_notify(self._read_uuid, self.notification_handler)

            LOGGER.info(f"Triggering update")

            await self.triggerStatus()
            #await asyncio.sleep(0.1)

        except (Exception) as error:
            self._is_on = None
            track = traceback.format_exc()
            LOGGER.debug(track)
            LOGGER.error(f"Error getting status: {error}")

    async def disconnect(self):
        LOGGER.debug("Disconnecting")
        if self._device.is_connected:
            await self._device.disconnect()