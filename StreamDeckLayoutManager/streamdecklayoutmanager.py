# SPDX-FileCopyrightText: 2022-present Didier Malenfant <coding@malenfant.net>
#
# SPDX-License-Identifier: GPLV3

import os

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper


# -- Classes
class Manager:
    """Manage all our Stream Deck interactions."""

    def __init__(self, config_file_path: str, deck_index: int = 0):
        """Initialize the manager based on user configuration."""

        self._config = None
        self._assets_folder = None
        self._font = None
        self._callbacks = {}
        self._pages = {}
        self._page_stack = []

        self._streamdecks = DeviceManager().enumerate()
        self._nb_of_streamdecks = len(self._streamdecks)

        if self._nb_of_streamdecks == 0:
            raise RuntimeError('StreamDeckLayoutManager: Couldn\'t find any streamdecks.')

        if deck_index >= self._nb_of_streamdecks:
            raise RuntimeError('StreamDeckLayoutManager: Ouf of bounds deck_index.')

        self._deck = self._streamdecks[deck_index]

        if not self._deck .is_visual():
            raise RuntimeError('StreamDeckLayoutManager: StreamDeck does not have any screene.')

        self._deck.open()
        self._deck.reset()

        self._initConfig(config_file_path)

        # -- Register callback function for when a key state changes.
        self._deck.set_key_callback(self._keyChangeCallback)

    def _initConfig(self, config_file_path: str):
        if config_file_path is None:
            raise RuntimeError('StreamDeckLayoutManager: Requires the path to its config file as an argument.')

        if not os.path.exists(config_file_path):
            raise RuntimeError(f'StreamDeckLayoutManager: Can\'t read config file at \'{config_file_path}\'.')

        try:
            with open(config_file_path, mode="rb") as fp:
                self._config = tomllib.load(fp)
        except Exception as e:
            raise RuntimeError(f'StreamDeckLayoutManager: Can\'t read config file at \'{config_file_path}\' ({e}).')

        config_data = self._config.get('config')
        if config_data is None:
            raise RuntimeError(f'StreamDeckLayoutManager: Missing \'config\' section in \'{config_file_path}\'.')

        folder_path = config_data.get('AssetFolder')
        if folder_path is None:
            raise RuntimeError(f'StreamDeckLayoutManager: Missing \'AssetFolder\' in \'{config_file_path}\'.')

        self._assets_folder = folder_path if folder_path.startswith('/') else os.path.join(os.path.join(Path(config_file_path).parent, folder_path))

        font_file = config_data.get('Font')
        if font_file is None:
            raise RuntimeError(f'StreamDeckLayoutManager: Missing \'Font\' in \'{config_file_path}\'.')

        font_size = config_data.get('FontSize')
        if font_size is None:
            raise RuntimeError(f'StreamDeckLayoutManager: Missing \'FontSize\' in \'{config_file_path}\'.')

        self._font = ImageFont.truetype(os.path.join(self._assets_folder, font_file), font_size)

        brightness = config_data.get('Brightness')
        if brightness is not None:
            self.setBrightness(brightness)

    # -- Generates a custom tile with run-time generated text and custom image via the PIL module.
    def _renderKeyImage(self, image_filename, label):
        # Resize the source image asset to best-fit the dimensions of a single key,
        # leaving a margin at the bottom so that we can draw the key title
        # afterwards.
        icon = Image.open(image_filename)
        image = PILHelper.create_scaled_image(self._deck, icon, margins=[0, 0, 20, 0])

        # Load a custom TrueType font and use it to overlay the key index, draw key
        # label onto the image a few pixels from the bottom of the key.
        draw = ImageDraw.Draw(image)
        draw.text((image.width / 2, image.height - 5), text=label, font=self._font, anchor='ms', fill='white')

        return PILHelper.to_native_format(self._deck, image)

    def _setKeyImage(self, key_index: int, image_file: str, label: str):
        image = None

        if image_file is not None:
            image_filename = image_file if image_file.startswith('/') else os.path.join(self._assets_folder, image_file)
            # Generate the custom key with the requested image and label.
            image = self._renderKeyImage(image_filename, label)
            if image is None:
                return

        # -- Use a scoped-with on the deck to ensure we're the only thread using it right now.
        with self._deck:
            # -- Update requested key with the generated image.
            self._deck.set_key_image(key_index, image)

    # -- Associated actions when a key is pressed.
    def _keyChangeCallback(self, deck, key_index, state):
        if self._deck is None or len(self._page_stack) == 0:
            return

        current_page_name = self._page_stack[-1]
        current_page_config = self._config.get(current_page_name)
        if current_page_config is None:
            return

        key_name = f'Key{key_index}'

        if state is True:
            action = current_page_config.get(key_name + 'PressedAction')
        else:
            action = current_page_config.get(key_name + 'ReleasedAction')

        if action is None or len(action) == 0:
            return

        callback_name = action[0]
        if callback_name == 'display_page':
            if len(action) != 2:
                raise RuntimeError('StreamDeckLayoutManager: Invalid arguments to display_page action.')

            self.displayPage(action[1])
        elif callback_name == 'push_page':
            if len(action) != 2:
                raise RuntimeError('StreamDeckLayoutManager: Invalid arguments to push_page action.')

            self.pushPage(action[1])
        elif callback_name == 'pop_page':
            if len(action) != 1:
                raise RuntimeError('StreamDeckLayoutManager: Invalid arguments to pop_page action.')

            self.popPage()
        else:
            callback = self._callbacks.get(callback_name)
            if callback is None:
                raise RuntimeError(f'StreamDeckLayoutManager: Unknown callback \'{callback_name}\'.')

            callback(action[1:])

    def _updatePage(self, page_name: str):
        page_config = self._config.get(page_name)
        if page_config is None:
            raise RuntimeError(f'StreamDeckLayoutManager: Missing config for page \'{page_name}\'.')

        for key_index in range(self._deck.key_count()):
            key_name = f'Key{key_index}'

            image_file = page_config.get(key_name + 'Image')
            label = page_config.get(key_name + 'Label')

            if image_file is not None and label is None:
                image_file = None

            self._setKeyImage(key_index, image_file, label)

    # -- Shutdown the manager.
    def shutdown(self):
        if self._deck is None:
            return

        # -- Use a scoped-with on the deck to ensure we're the only thread using it right now.
        with self._deck:
            # -- Reset deck, clearing all button images.
            self._deck.reset()

            # -- Close deck handle, terminating internal worker threads.
            self._deck.close()

        self._deck = None

    def setBrightness(self, percentage: int):
        if self._deck is None:
            return

        self._deck.set_brightness(percentage)

    def displayPage(self, page_name: str):
        if self._deck is None:
            return

        current_page_index = len(self._page_stack) - 1
        if current_page_index < 0:
            self._page_stack.append(page_name)
            current_page_index = 0
        else:
            self._page_stack[current_page_index] = page_name

        self._updatePage(page_name)

    def pushPage(self, page_name: str):
        if self._deck is None:
            return

        current_page_index = len(self._page_stack) - 1
        if current_page_index < 0:
            raise RuntimeError('StreamDeckLayoutManager: No current page set before calling pushPage().')

        self._page_stack.append(page_name)

        self._updatePage(page_name)

    def popPage(self):
        if self._deck is None:
            return

        current_page_index = len(self._page_stack) - 1
        if current_page_index < 1:
            raise RuntimeError('StreamDeckLayoutManager: No page to pop when calling popPage().')

        self._page_stack = self._page_stack[:-1]

        self._updatePage(self._page_stack[-1])

    def setKey(self, page_name: str, key_index: int, image_file: str, label: str, pressed_callback, released_callback=None):
        if self._deck is None:
            return

        page_config = self._config.get(page_name)
        if page_config is None:
            return

        key_name = f'Key{key_index}'
        parameter = key_name + 'Image'
        if image_file is None:
            if parameter in page_config:
                page_config.pop(parameter)
        else:
            page_config[parameter] = image_file

        parameter = key_name + 'Label'
        if label is None:
            if parameter in page_config:
                page_config.pop(parameter)
        else:
            page_config[parameter] = label

        parameter = key_name + 'PressedAction'
        if pressed_callback is None:
            if parameter in page_config:
                page_config.pop(parameter)
        else:
            page_config[parameter] = pressed_callback

        parameter = key_name + 'ReleasedAction'
        if released_callback is None:
            if parameter in page_config:
                page_config.pop(parameter)
        else:
            page_config[parameter] = released_callback

        # -- If we are currently displaying this page then we update the button too
        if len(self._page_stack) > 0 and page_name == self._page_stack[-1]:
            self._setKeyImage(key_index, image_file, label)

    def setCallback(self, callback_name: str, callback):
        if self._deck is None:
            return

        reserved_callback_names = ['display_page', 'push_page', 'pop_page']
        if callback_name in reserved_callback_names:
            raise RuntimeError(f'StreamDeckLayoutManager: Callback name \'{callback_name}\' is reserved.')

        if callback is None:
            self._callbacks.pop(callback_name)
        else:
            self._callbacks[callback_name] = callback

    # -- Return the number of stream decks found.
    def numberOfStreamDecks(self) -> int:
        return self.number_of_streamdecks

    # -- Prints diagnostic information about a given StreamDeck.
    def printDeckInfo(self, index: int):
        if index >= self.number_of_streamdecks:
            raise RuntimeError('Out of bounds index for printDeckInof().')

        deck = self._decks[index]
        image_format = deck.key_image_format()

        flip_description = {
            (False, False): 'not mirrored',
            (True, False): 'mirrored horizontally',
            (False, True): 'mirrored vertically',
            (True, True): 'mirrored horizontally/vertically',
        }

        print('Deck {} - {}.'.format(index, deck.deck_type()))
        print('\t - ID: {}'.format(deck.id()))
        print('\t - Serial: \'{}\''.format(deck.get_serial_number()))
        print('\t - Firmware Version: \'{}\''.format(deck.get_firmware_version()))
        print('\t - Key Count: {} (in a {}x{} grid)'.format(
            deck.key_count(),
            deck.key_layout()[0],
            deck.key_layout()[1]))
        if deck.is_visual():
            print('\t - Key Images: {}x{} pixels, {} format, rotated {} degrees, {}'.format(
                image_format['size'][0],
                image_format['size'][1],
                image_format['format'],
                image_format['rotation'],
                flip_description[image_format['flip']]))
        else:
            print('\t - No Visual Output')
