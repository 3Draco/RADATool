import tkinter as tk
from tkinter import ttk, filedialog, messagebox, Listbox, Scrollbar
import requests
import json
from datetime import datetime
import time
import configparser
import os
import sys
from base64 import urlsafe_b64encode, urlsafe_b64decode
import glob # Import for finding files easily
import threading # Import the threading module

# API Constants
API_BASE_URL = "https://retroachievements.org/API/"
API_USER_PROFILE_URL = API_BASE_URL + "API_GetUserProfile.php"
API_CONSOLE_IDS_URL = API_BASE_URL + "API_GetConsoleIDs.php"
API_GAME_LIST_URL = API_BASE_URL + "API_GetGameList.php"
API_GET_GAME_HASHES_URL = API_BASE_URL + "API_GetGameHashes.php"
API_GET_GAME_EXTENDED_URL = API_BASE_URL + "API_GetGameExtended.php"

class RetroAchievementsDATGenerator:
    def __init__(self, master):
        self.master = master
        # Initial title - will be updated by localization
        master.title("RADATool - RetroAchievements DAT/Collection Tool")
        master.geometry("650x750") # Increased height for new layout
        master.minsize(650, 750) # Set minimum size

        # Initialize paths and directories
        self.script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.config_file = os.path.join(self.script_dir, "settings.ini")
        self.cache_dir = os.path.join(self.script_dir, "cache")
        # Language directory
        self.lang_dir = os.path.join(self.script_dir, "lang")

        # Create cache directory if it doesn't exist
        os.makedirs(self.cache_dir, exist_ok=True)
        # Ensure language directory exists (optional, but good practice)
        os.makedirs(self.lang_dir, exist_ok=True)


        # --- Initialize ALL Tkinter variables FIRST ---
        # These defaults will be overwritten by load_config if values exist in the INI

        # AUTH
        self.username = tk.StringVar(value='')
        self.api_key = tk.StringVar(value='')
        self.selected_console_id_var = tk.StringVar() # Stores console Name

        # PATHS
        self.dat_save_path = tk.StringVar(value=self.script_dir)
        self.collection_cfg_save_path = tk.StringVar(value=self.script_dir)
        self.retropie_base_path = tk.StringVar(value="/home/pi/RetroPie/roms")
        # Add Batocera base path variable
        self.batocera_base_path = tk.StringVar(value="/userdata/roms") # Common Batocera path


        # OPTIONS
        # OLD: self.roms_are_zipped_var = tk.BooleanVar(value=False)
        # NEW: StringVar for ROM extension, default to .zip
        self.rom_extension_var = tk.StringVar(value=".zip")
        self.include_achievements_var = tk.BooleanVar(value=True)
        self.include_patch_urls_var = tk.BooleanVar(value=True)

        # Language variable (initialized here with default 'en')
        self.selected_language_code_var = tk.StringVar(value='en')

        # Other internal variables
        self.status_bar_text_var = tk.StringVar(value="") # Will be set by localization
        self.status_bar = None # Will be initialized in setup_ui

        # Fortschrittsfenster und Labels als Instanzvariablen initialisieren
        self._fetch_progress_popup = None
        self.fetch_progress_label_var = tk.StringVar(value="")
        self._dat_progress_popup = None
        self.dat_progress_label_var = tk.StringVar(value="")
        self._collection_progress_popup = None # Used for both collection types
        self.collection_progress_label_var = tk.StringVar(value="") # Used for both collection types

        # Variable to hold the fetch worker thread
        self._fetch_worker_thread = None


        # Map to store console ID to Name mapping (populated after login/load_consoles)
        self.console_id_to_name_map = {}
        # Reverse map (Name to ID)
        self.console_name_to_id_map = {}

        # In-memory cache for fetched game data
        self.cached_data = {}
        # --- End Initialize ALL Tkinter variables ---


        # --- Language Initialization ---
        # Load configuration to get the saved language (now the variables exist)
        self.config = configparser.ConfigParser()
        self.load_config() # This will load saved language into selected_language_code_var and the new rom_extension_var

        # Find available languages BEFORE loading the UI
        self.find_available_languages()

        # Load the determined language (from config or default)
        # This must happen before setup_ui so UI elements get translated strings immediately
        # Also ensure the loaded language code is valid and fallback if necessary
        saved_lang_code = self.selected_language_code_var.get()
        if saved_lang_code not in self.available_languages:
            print(f"Warning: Saved language code '{saved_lang_code}' not found in available languages. Falling back to 'en'.")
            saved_lang_code = 'en'
            self.selected_language_code_var.set('en') # Update variable and save config immediately
            self.save_config()

        self.load_language(saved_lang_code)
        # --- End Language Initialization ---


        # Setup UI
        self.setup_ui()

        # Update UI elements with loaded translations after they are created
        self.update_ui_language()

        # Auto-login if credentials exist (check variables AFTER loading config)
        if self.username.get() and self.api_key.get():
            self.master.after(100, self.test_login)
        else:
            # Set initial status bar text after UI is setup and language is loaded
            self.status_bar_text_var.set(self.translate("status_ready"))


    def translate(self, key, *args):
        """Looks up a translation key and formats it with args."""
        translation = self.translations.get(key, f"MISSING_TRANSLATION:{key}")
        try:
            # Apply arguments if any
            if args:
                 # Use %s formatting for simplicity with configparser values
                 # Note: configparser reads values as strings, so formatting with %s is generally safe
                 # For numbers (like %d, %.2f), the translation string must contain the correct format specifier
                 return translation % args
            return translation
        except (TypeError, ValueError) as e:
            # Handle cases where formatting fails (e.g., wrong number/type of args)
            print(f"Warning: Failed to format translation for key '{key}' with args {args}. Translation: '{translation}'. Error: {e}")
            return translation # Return the raw translation string


    def find_available_languages(self):
        """Scans the 'lang' directory for .ini files and populates available_languages."""
        self.available_languages = {}
        try:
            # List files in the language directory
            if not os.path.isdir(self.lang_dir):
                print(f"Warning: Language directory not found: {self.lang_dir}")
                # If the directory doesn't exist, create it and inform the user
                try:
                    os.makedirs(self.lang_dir, exist_ok=True)
                    print(f"Created language directory: {self.lang_dir}. Please add language INI files here.")
                except Exception as create_err:
                    print(f"Error creating language directory: {create_err}")
                self.available_languages = {'en': 'English'} # Fallback
                return # Exit if dir wasn't found/created

            lang_files = glob.glob(os.path.join(self.lang_dir, "*.ini"))
            for lang_file in lang_files:
                lang_code = os.path.splitext(os.path.basename(lang_file))[0].lower()
                # Read the language name from the file itself if available
                lang_config = configparser.ConfigParser()
                try:
                    # Use read(filename, encoding) for better compatibility
                    with open(lang_file, 'r', encoding='utf-8') as f:
                         lang_config.read_file(f)
                    # Get language name from [Language] section
                    lang_name = lang_config.get('Language', 'name', fallback=lang_code) # Section [Language], key 'name'
                    self.available_languages[lang_code] = lang_name
                except Exception as e:
                    print(f"Warning: Could not read language name from {lang_file}: {e}")
                    self.available_languages[lang_code] = lang_code # Fallback to code if reading fails

            # Ensure a fallback language (like 'en') is always available if files are missing
            if not self.available_languages:
                 self.available_languages['en'] = 'English'
                 print("Warning: No language files found in 'lang' directory. Falling back to English.")

            # Sort languages by name
            self.available_languages = dict(sorted(self.available_languages.items(), key=lambda item: item[1]))

            print(f"DEBUG: Found available languages: {self.available_languages}")

        except Exception as e:
            print(f"Error scanning language directory: {e}")
            self.available_languages = {'en': 'English'} # Fallback to English on error


    def load_language(self, lang_code):
        """Loads translations from the specified language code's INI file."""
        lang_file = os.path.join(self.lang_dir, f"{lang_code}.ini")
        new_translations = {}
        success = False

        # Try loading the specified language
        if os.path.exists(lang_file):
            lang_config = configparser.ConfigParser()
            try:
                 with open(lang_file, 'r', encoding='utf-8') as f:
                    lang_config.read_file(f)
                 if 'Translations' in lang_config:
                    new_translations = dict(lang_config['Translations'])
                    self.translations = new_translations # Update the main translations dictionary
                    # self.selected_language_code_var.set(lang_code) # Variable is already set before calling this
                    success = True
                    print(f"DEBUG: Successfully loaded language: {lang_code}")
                 else:
                     print(f"Warning: '{lang_file}' is missing the '[Translations]' section.")
            except Exception as e:
                 print(f"Error reading language file '{lang_file}': {e}")

        # If loading the specified language failed AND it wasn't already 'en', try the default ('en')
        if not success and lang_code != 'en':
            print(f"Warning: Failed to load language '{lang_code}'. Attempting to load default 'en'.")
            default_lang_file = os.path.join(self.lang_dir, "en.ini")
            if os.path.exists(default_lang_file):
                 default_lang_config = configparser.ConfigParser()
                 try:
                      with open(default_lang_file, 'r', encoding='utf-8') as f:
                        default_lang_config.read_file(f)
                      if 'Translations' in default_lang_config:
                         self.translations = dict(default_lang_config['Translations'])
                         self.selected_language_code_var.set('en') # Set variable to default on success
                         success = True
                         print("DEBUG: Successfully loaded default language: en")
                      else:
                          print(f"Warning: '{default_lang_file}' is missing the '[Translations]' section.")
                 except Exception as e:
                     print(f"Error reading default language file '{default_lang_file}': {e}")
            else:
                 print(f"Warning: Default language file 'en.ini' not found.")

        # If both fail, use empty translations (will show keys)
        if not success:
             print("Error: Could not load any language file. Using empty translations.")
             self.translations = {}
             self.selected_language_code_var.set('en') # Still set to 'en' logically as fallback


        # Update UI after loading new translations
        # This is now called from __init__ and on_language_selected


    def update_ui_language(self):
        """Updates all UI elements with the currently loaded translations."""
        # Update Window Title
        #self.master.title(self.translate("window_title"))

        # Update Labels and Buttons (requires accessing the created widgets)
        # This part is tedious - you need to update every widget you created.
        # Make sure you have stored references to these widgets in self.

        # Login Frame
        if hasattr(self, 'login_frame'):
             self.login_frame.config(text=self.translate("login_frame_title"))
        if hasattr(self, 'username_label'):
            self.username_label.config(text=self.translate("username_label"))
        if hasattr(self, 'api_key_label'):
             self.api_key_label.config(text=self.translate("api_key_label"))
        if hasattr(self, 'login_button'):
            self.login_button.config(text=self.translate("login_button"))
        if hasattr(self, 'logout_button'):
            self.logout_button.config(text=self.translate("logout_button"))
        # Update status labels based on current state indicator (the color)
        if hasattr(self, 'login_status_label') and hasattr(self, 'login_status_light'):
            current_color = self.login_status_light.cget("bg")
            if current_color == "green":
                self.login_status_label.config(text=self.translate("status_connected"))
            elif current_color == "red":
                # Check if status bar indicates a specific failure reason first
                current_status_text = self.status_bar_text_var.get()
                if self.translate("login_failed_input_missing") in current_status_text or \
                   self.translate("status_auth_failed_missing") in current_status_text:
                    self.login_status_label.config(text=self.translate("status_error")) # Use generic Error for login issues
                else:
                     self.login_status_label.config(text=self.translate("status_disconnected")) # Default to disconnected if red
            # Add more checks if you have other distinct status colors/texts


        # System & Data Fetch Frame
        if hasattr(self, 'system_data_frame'):
             self.system_data_frame.config(text=self.translate("system_data_frame_title"))
        if hasattr(self, 'select_system_label'):
             self.select_system_label.config(text=self.translate("select_system_label"))
        if hasattr(self, 'cache_manager_button'):
             self.cache_manager_button.config(text=self.translate("cache_manager_button"))
        # Check if checkboxes exist before updating (optional safety)
        if hasattr(self, 'include_achievements_cb'):
             self.include_achievements_cb.config(text=self.translate("include_achievements_checkbox"))
        if hasattr(self, 'include_patch_urls_cb'):
             self.include_patch_urls_cb.config(text=self.translate("include_patch_urls_checkbox"))

        if hasattr(self, 'fetch_data_button'):
            self.fetch_data_button.config(text=self.translate("fetch_data_button"))


        # DAT Creation Frame
        if hasattr(self, 'dat_creation_frame'):
             self.dat_creation_frame.config(text=self.translate("dat_creation_frame_title"))
        if hasattr(self, 'dat_save_path_label'):
             self.dat_save_path_label.config(text=self.translate("dat_save_location_label"))
        if hasattr(self, 'browse_dat_button'):
             self.browse_dat_button.config(text=self.translate("browse_button"))
        if hasattr(self, 'create_dat_button'):
            self.create_dat_button.config(text=self.translate("create_dat_button"))


        # Collection Creation Frame (Renamed)
        if hasattr(self, 'collection_creation_frame'): # Use the new variable
             self.collection_creation_frame.config(text=self.translate("collection_creation_frame_title")) # Use the new key
        if hasattr(self, 'retropie_path_label'):
             self.retropie_path_label.config(text=self.translate("retropie_rom_path_label"))
        # Update the new Batocera path label
        if hasattr(self, 'batocera_path_label'):
             self.batocera_path_label.config(text=self.translate("batocera_rom_path_label")) # New translation key
        if hasattr(self, 'browse_retropie_button'):
             self.browse_retropie_button.config(text=self.translate("browse_button"))
        # Update the new Batocera browse button
        if hasattr(self, 'browse_batocera_button'):
             self.browse_batocera_button.config(text=self.translate("browse_button"))
        if hasattr(self, 'collection_cfg_save_path_label'):
             self.collection_cfg_save_path_label.config(text=self.translate("collection_cfg_save_path_label"))
        if hasattr(self, 'browse_collection_button'):
             self.browse_collection_button.config(text=self.translate("browse_button"))

        # Updated: Rom Extension Label and Entry
        if hasattr(self, 'rom_extension_label'): # New label reference
            self.rom_extension_label.config(text=self.translate("rom_extension_label")) # New translation key
        # No text to update for the entry field itself, it uses textvariable

        # OLD: if hasattr(self, 'roms_are_zipped_cb'):
        # OLD: self.roms_are_zipped_cb.config(text=self.translate("roms_are_zipped_checkbox"))


        # Update the RetroPie and Batocera collection buttons (variables changed)
        if hasattr(self, 'create_retropie_collection_button'):
            self.create_retropie_collection_button.config(text=self.translate("create_retropie_collection_button")) # Updated translation key
        if hasattr(self, 'create_batocera_collection_button'):
            self.create_batocera_collection_button.config(text=self.translate("create_batocera_collection_button")) # New translation key

        # NEW: About Button update
        if hasattr(self, 'about_button'): # Ensure the button exists before trying to update it
            self.about_button.config(text=self.translate("about_button")) # Use the new translation key

        # Status Bar
        # Status bar text var is updated directly where needed, but initial text can be set here
        # self.status_bar_text_var.set(self.translate("status_ready")) # Handled in __init__

        # --- Language Dropdown ---
        # Update the values in the language dropdown
        if hasattr(self, 'language_dropdown'):
             # Get the list of language names from the available languages dictionary
             language_names = list(self.available_languages.values())
             self.language_dropdown['values'] = language_names

             # Try to keep the current selection by its name
             current_code = self.selected_language_code_var.get()
             current_name = self.available_languages.get(current_code, current_code) # Get name, fallback to code

             if current_name in language_names:
                  self.language_dropdown.set(current_name)
             elif language_names:
                  self.language_dropdown.set(language_names[0]) # Set to first available if current is gone
                  # If we fell back, update the internal variable as well
                  fallback_code = list(self.available_languages.keys())[0]
                  if fallback_code != current_code:
                     self.selected_language_code_var.set(fallback_code)
                     self.save_config() # Save the new fallback selection
             else:
                  self.language_dropdown.set("") # No languages available

        self.language_dropdown.bind("<<ComboboxSelected>>", self.on_language_selected)


        # Update pop-up window titles and labels if they are open
        # These checks need to be robust in case the popups are in various states
        # Note: Progress labels themselves are often updated within the worker/creation functions
        # This just updates the title if the window happens to be open during a language change
        if hasattr(self, '_fetch_progress_popup') and self._fetch_progress_popup and tk.Toplevel.winfo_exists(self._fetch_progress_popup):
             self._fetch_progress_popup.title(self.translate("data_fetch_progress_title"))
             # Also update the specific labels within if they exist
             if hasattr(self, 'fetch_progress_label_var'):
                  # You might need to re-set the current state message here
                  # For simplicity, setting a generic 'updating' message might suffice,
                  # or you need to store the last specific message state.
                  # For now, rely on the variables being updated later if needed.
                  pass # Specific progress text updated elsewhere

        if hasattr(self, '_dat_progress_popup') and self._dat_progress_popup and tk.Toplevel.winfo_exists(self._dat_progress_popup):
             self._dat_progress_popup.title(self.translate("dat_creation_progress_title"))
             if hasattr(self, 'dat_progress_label_var'):
                  pass # Specific progress text updated elsewhere

        # Updated check for collection progress popup title (using generic key)
        if hasattr(self, '_collection_progress_popup') and self._collection_progress_popup and tk.Toplevel.winfo_exists(self._collection_progress_popup):
             # Use a generic translation key for the title as it's used for both now
             # The specific process message will be in the label_var
             self._collection_progress_popup.title(self.translate("collection_creation_progress_title"))
             if hasattr(self, 'collection_progress_label_var'):
                  pass # Specific progress text updated elsewhere


        # Cache Manager Dialog Update
        if hasattr(self, '_cache_manager_popup') and self._cache_manager_popup and tk.Toplevel.winfo_exists(self._cache_manager_popup):
             self._cache_manager_popup.title(self.translate("cache_dialog_title"))
             # Assuming labels within the cache manager have attributes
             if hasattr(self, 'cache_count_label') and hasattr(self, '_cache_file_paths'):
                 self.cache_count_label.config(text=self.translate("cache_file_count_label", len(self._cache_file_paths)))
             if hasattr(self, 'cache_size_label') and hasattr(self, 'total_size_mb_cache_dialog'): # Assuming you store total size
                 # Re-calculate total size if needed or store it
                 cache_files_recheck = glob.glob(os.path.join(self.cache_dir, "console_*.json"))
                 total_size_mb_recheck = sum(os.path.getsize(f) for f in cache_files_recheck if os.path.isfile(f)) / (1024 * 1024)
                 self.cache_size_label.config(text=self.translate("cache_total_size_label", total_size_mb_recheck))
             if hasattr(self, 'cache_listbox'):
                 # Rebuild listbox content display strings
                 cache_info_list_recheck = []
                 cache_files_recheck = glob.glob(os.path.join(self.cache_dir, "console_*.json"))
                 if os.path.isdir(self.cache_dir):
                    for file_path in cache_files_recheck:
                         if os.path.isfile(file_path):
                            filename = os.path.basename(file_path)
                            try:
                                file_size_bytes = os.path.getsize(file_path)
                                file_size_mb = file_size_bytes / (1024 * 1024)
                                console_id = filename.replace("console_", "").replace(".json", "")
                                console_id_str = str(console_id) if console_id else None
                                console_name = self.console_id_to_name_map.get(console_id_str, f"ID {console_id if console_id else 'N/A'}")
                                display_text = f"{console_name} ({filename} - {file_size_mb:.2f} MB)"
                                cache_info_list_recheck.append((display_text, file_path))
                            except Exception as e:
                                print(self.translate("cache_processing_error_display", filename, e))
                                try:
                                    file_size_bytes = os.path.getsize(file_path)
                                    file_size_mb = file_size_bytes / (1024 * 1024)
                                    display_text = f"{filename} ({self.translate('cache_size_label', file_size_mb):s})"
                                    cache_info_list_recheck.append((display_text, file_path))
                                except:
                                     display_text = f"{filename} ({self.translate('cache_unknown_size')})"
                                     cache_info_list_recheck.append((display_text, file_path))

                 cache_info_list_recheck.sort(key=lambda item: item[0])
                 self.cache_listbox.delete(0, tk.END)
                 for display_text, _ in cache_info_list_recheck:
                     self.cache_listbox.insert(tk.END, display_text)
                 self._cache_file_paths = [file_path for _, file_path in cache_info_list_recheck]

             # Update buttons within the cache dialog if you stored references to them
             if hasattr(self, 'cache_select_all_cb'): # Example, assuming you store the checkbox
                 self.cache_select_all_cb.config(text=self.translate("cache_select_all_checkbox"))
             if hasattr(self, 'cache_delete_selected_button_ref'): # Example
                 self.cache_delete_selected_button_ref.config(text=self.translate("cache_delete_selected_button"))
             if hasattr(self, 'cache_close_button_ref'): # Example
                 self.cache_close_button_ref.config(text=self.translate("cache_close_button"))

        # About Dialog Update (new)
        if hasattr(self, '_about_popup') and self._about_popup and tk.Toplevel.winfo_exists(self._about_popup):
            self._about_popup.title(self.translate("about_dialog_title"))
            # Re-create or update content of labels within the about dialog if they exist and are translatable
            if hasattr(self, '_about_label_version'):
                self._about_label_version.config(text=self.translate("about_version_text", "1.0"))
            if hasattr(self, '_about_label_author'):
                self._about_label_author.config(text=self.translate("about_author_text", "3Draco"))
            if hasattr(self, '_about_label_thanks'):
                self._about_label_thanks.config(text=self.translate("about_thanks_text"))


    def load_config(self):
        """Load configuration from INI file and update Tkinter variables."""
        # No change needed here, reading config doesn't need newline=''
        self.config.read(self.config_file, encoding='utf-8') # Specify encoding

        # If file doesn't exist, populate config object with defaults and save
        if not os.path.exists(self.config_file) or not self.config.sections():
            print("DEBUG: Creating new default config file.")
            self.config['AUTH'] = {'username': '', 'api_key': ''}
            self.config['PATHS'] = {
                'dat_save_path': self.script_dir,
                'collection_cfg_save_path': self.script_dir,
                'retropie_base_path': "/home/pi/RetroPie/roms",
                'batocera_base_path': "/userdata/roms" # Add default Batocera path
            }
            self.config['OPTIONS'] = {
                # OLD: 'roms_are_zipped': 'no',
                # NEW: Default rom extension
                'rom_extension': '.zip',
                'include_achievements': 'yes',
                'include_patch_urls': 'yes'
            }
            # Add SETTINGS section for language
            self.config['SETTINGS'] = {
                 'language': 'en' # Default language in new config
            }
            # Save the newly created default config
            # Ensure save_config uses newline='' for INI if needed (not requested, keep as is)
            self.save_config()
            # Set the default language variable
            self.selected_language_code_var.set('en')
            # No need to update other variables from config here, as they already have defaults

        # If file exists, read values and update Tkinter variables
        # Use get() methods with fallbacks to handle missing sections/keys gracefully
        if 'AUTH' in self.config:
            self.username.set(self._decrypt(self.config.get('AUTH', 'username', fallback='')))
            self.api_key.set(self._decrypt(self.config.get('AUTH', 'api_key', fallback='')))

        if 'PATHS' in self.config:
            self.dat_save_path.set(os.path.normpath(self.config.get('PATHS', 'dat_save_path', fallback=self.script_dir)))
            self.collection_cfg_save_path.set(os.path.normpath(self.config.get('PATHS', 'collection_cfg_save_path', fallback=self.script_dir)))
            self.retropie_base_path.set(os.path.normpath(self.config.get('PATHS', 'retropie_base_path', fallback="/home/pi/RetroPie/roms")))
            # Load Batocera base path
            self.batocera_base_path.set(os.path.normpath(self.config.get('PATHS', 'batocera_base_path', fallback="/userdata/roms")))

        if 'OPTIONS' in self.config:
            # OLD: self.roms_are_zipped_var.set(self.config.getboolean('OPTIONS', 'roms_are_zipped', fallback=False))
            # NEW: Load rom extension
            self.rom_extension_var.set(self.config.get('OPTIONS', 'rom_extension', fallback='.zip').strip())
            self.include_achievements_var.set(self.config.getboolean('OPTIONS', 'include_achievements', fallback=True))
            self.include_patch_urls_var.set(self.config.getboolean('OPTIONS', 'include_patch_urls', fallback=True))

        # Read language setting
        if 'SETTINGS' in self.config:
             # Get the language code, fallback to 'en'
             saved_lang_code = self.config.get('SETTINGS', 'language', fallback='en').lower()
             self.selected_language_code_var.set(saved_lang_code)
        else:
             # If SETTINGS section is missing, add it and save, default to 'en'
             self.config['SETTINGS'] = {'language': 'en'}
             # Ensure save_config uses newline='' for INI if needed (not requested, keep as is)
             self.save_config()
             self.selected_language_code_var.set('en')


    def save_config(self):
        """Save current configuration (including credentials, paths, options, and language) to INI file"""
        # Ensure sections exist before attempting to set keys, especially if starting from an empty config
        if 'AUTH' not in self.config: self.config['AUTH'] = {}
        if 'PATHS' not in self.config: self.config['PATHS'] = {}
        if 'OPTIONS' not in self.config: self.config['OPTIONS'] = {}
        if 'SETTINGS' not in self.config: self.config['SETTINGS'] = {} # Ensure SETTINGS section exists

        # Update config object from current UI variables
        # These variables are now guaranteed to exist because they are initialized in __init__
        self.config['AUTH']['username'] = self._encrypt(self.username.get())
        self.config['AUTH']['api_key'] = self._encrypt(self.api_key.get())

        self.config['PATHS']['dat_save_path'] = os.path.normpath(self.dat_save_path.get())
        self.config['PATHS']['collection_cfg_save_path'] = os.path.normpath(self.collection_cfg_save_path.get())
        self.config['PATHS']['retropie_base_path'] = os.path.normpath(self.retropie_base_path.get())
        # Save Batocera base path
        self.config['PATHS']['batocera_base_path'] = os.path.normpath(self.batocera_base_path.get())


        self.config['OPTIONS']['include_achievements'] = 'yes' if self.include_achievements_var.get() else 'no'
        self.config['OPTIONS']['include_patch_urls'] = 'yes' if self.include_patch_urls_var.get() else 'no'
        # NEW: Save rom extension
        self.config['OPTIONS']['rom_extension'] = self.rom_extension_var.get().strip()


        # Save language setting
        self.config['SETTINGS']['language'] = self.selected_language_code_var.get()


        try:
            # User requested NOT to change settings.ini line endings, keep as is
            with open(self.config_file, 'w', encoding='utf-8') as configfile: # Specify encoding
                self.config.write(configfile)
            # print(f"DEBUG: Configuration saved to {self.config_file}") # Optional debug print
        except IOError as e:
            print(f"ERROR: Failed to save configuration to {self.config_file}: {e}")
            self.status_bar_text_var.set(self.translate("status_config_save_error", self.config_file, e)) # Add this key


    def clear_credentials(self):
        """Clear stored credentials and save config"""
        self.username.set('')
        self.api_key.set('')
        # Also clear them in the config object before saving
        if 'AUTH' in self.config:
            self.config['AUTH']['username'] = ''
            self.config['AUTH']['api_key'] = ''
        self.save_config() # Save the cleared credentials

        self.login_status_light.config(bg="red")
        self.login_status_label.config(text=self.translate("status_disconnected")) # Use translated text
        self.console_dropdown.config(state="disabled")
        self.console_dropdown.set('')
        self.console_id_to_name_map = {} # Clear mappings on logout
        self.console_name_to_id_map = {}
        self.cached_data = {} # Clear in-memory cache on logout
        # Disable all action buttons
        self.fetch_data_button.config(state="disabled")
        self.create_dat_button.config(state="disabled")
        # Use the new button variables
        self.create_retropie_collection_button.config(state="disabled")
        self.create_batocera_collection_button.config(state="disabled")

        self.status_bar_text_var.set(self.translate("clear_credentials_status")) # Use translated text
        self.on_selection_change(None)


    def _encrypt(self, text):
        """Simple 'encryption' (obfuscation) for sensitive data"""
        if not text:
            return ''
        try:
            return urlsafe_b64encode(text.encode('utf-8')).decode() # Ensure utf-8 encoding
        except Exception:
            # Handle potential encoding errors if input is not utf-8
            print("Warn: Failed to encrypt text.") # Simplified warning
            return ''


    def _decrypt(self, text):
        """'Decryption' for stored data"""
        if not text:
            return ''
        try:
            return urlsafe_b64decode(text.encode('utf-8')).decode() # Ensure utf-8 encoding
        except Exception:
            print("Warn: Failed to decrypt text (invalid base64 or encoding?).") # Simplified warning
            return '' # Return empty string on error


    def get_cache_filename(self, console_id):
        """Generate cache filename for console"""
        # Ensure console_id is treated as string for path creation
        return os.path.join(self.cache_dir, f"console_{str(console_id)}.json")

    def load_from_cache(self, console_id):
        """Load data from cache if available and valid"""
        # Caching is always on now, so we just attempt to load
        cache_file = self.get_cache_filename(console_id)
        if os.path.exists(cache_file) and os.path.isfile(cache_file) and os.path.getsize(cache_file) > 2: # Check > 2 bytes to avoid empty json []
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    print(self.translate("data_fetch_loading_from_cache", os.path.basename(cache_file))) # Use translated text
                    data = json.load(f)
                    if isinstance(data, list):
                        # self.status_bar_text_var.set(self.translate("status_cache_loaded", os.path.basename(cache_file))) # Avoid overwriting status
                        return data
                    else:
                        print(self.translate("cache_load_error_list", cache_file)) # Use translated text
                        # self.status_bar_text_var.set(self.translate("status_cache_invalid_list", os.path.basename(cache_file))) # Avoid overwriting status
                        return None
            except json.JSONDecodeError as e:
                print(self.translate("cache_load_error_json_parse", cache_file, str(e))) # Use translated text
                # self.status_bar_text_var.set(self.translate("status_cache_json_error", os.path.basename(cache_file))) # Avoid overwriting status
                return None
            except Exception as e:
                print(self.translate("cache_load_error_general", cache_file, str(e))) # Use translated text
                # self.status_bar_text_var.set(self.translate("status_cache_read_error", str(e))) # Avoid overwriting status
                return None
        # print(f"Keine Cache-Datei gefunden oder es ist kein File oder leer: {cache_file}") # This message might be ok as is, or add a key
        return None

    def save_to_cache(self, console_id, data):
        """Save data to cache with improved error handling"""
        # Caching is always on now, so we just attempt to save
        cache_file = self.get_cache_filename(console_id)
        try:
            if not isinstance(data, list):
                print(self.translate("cache_save_invalid_data_type", console_id)) # Use translated text
                self.status_bar_text_var.set(self.translate("cache_save_invalid_data_structure_status")) # Use translated text
                return False
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            # User requested NOT to change cache file line endings, keep as is
            with open(cache_file, 'w', encoding='utf-8') as f: # Specify encoding
                json.dump(data, f, ensure_ascii=False, indent=2)
            # print(f"Daten erfolgreich im Cache gespeichert: {cache_file}") # This message might be ok as is, or add a key
            # self.status_bar_text_var.set(self.translate("status_cache_saved", os.path.basename(cache_file))) # Avoid overwriting status
            return True
        except IOError as e:
            print(self.translate("cache_io_error", cache_file, str(e))) # Use translated text
            self.status_bar_text_var.set(self.translate("status_cache_io_error", str(e))) # Use translated text
            return False
        except Exception as e:
            print(self.translate("cache_general_error", cache_file, str(e))) # Use translated text
            self.status_bar_text_var.set(self.translate("status_cache_general_error", str(e))) # Use translated text
            return False

    def show_cache_manager_dialog(self):
        """Shows a dialog to view cache info and selectively delete cache files."""
        # Store a reference to the popup window
        self._cache_manager_popup = tk.Toplevel(self.master)
        popup = self._cache_manager_popup # Use local name for convenience

        cache_path = self.cache_dir
        cache_files = glob.glob(os.path.join(self.cache_dir, "console_*.json"))
        cache_info_list = [] # List of (display_text, file_path)
        total_size_mb = 0

        # Gather info for display and deletion mapping
        if os.path.isdir(cache_path):
            for file_path in cache_files:
                 if os.path.isfile(file_path):
                    filename = os.path.basename(file_path)
                    try:
                        file_size_bytes = os.path.getsize(file_path)
                        file_size_mb = file_size_bytes / (1024 * 1024)
                        total_size_mb += file_size_mb

                        # Try to get console name
                        console_id = filename.replace("console_", "").replace(".json", "")
                        # Ensure console_id is a valid key type (string)
                        console_id_str = str(console_id) if console_id else None

                        console_name = self.console_id_to_name_map.get(console_id_str, f"ID {console_id if console_id else 'N/A'}")


                        display_text = f"{console_name} ({filename} - {file_size_mb:.2f} MB)"
                        cache_info_list.append((display_text, file_path))
                    except Exception as e:
                        print(self.translate("cache_processing_error_display", filename, e)) # Use translated text
                        # Add entry with filename if error
                        try:
                            file_size_bytes = os.path.getsize(file_path)
                            file_size_mb = file_size_bytes / (1024 * 1024)
                            total_size_mb += file_size_mb
                            display_text = f"{filename} ({self.translate('cache_size_label', file_size_mb):s})" # Format string like "Size: %.2f MB"
                            cache_info_list.append((display_text, file_path))
                        except:
                             display_text = f"{filename} ({self.translate('cache_unknown_size')})" # Use translated text
                             cache_info_list.append((display_text, file_path))


        # Sort cache_info_list by display text
        cache_info_list.sort(key=lambda item: item[0])

        # --- Configure the popup window ---
        popup.title(self.translate("cache_dialog_title")) # Use translated text
        # popup.geometry("500x500") # Removed fixed geometry here
        popup.resizable(False, False)

        # --- Calculate center position relative to the main window ---
        # Ensure main window size is calculated
        self.master.update_idletasks()
        main_width = self.master.winfo_width()
        main_height = self.master.winfo_height()
        main_x = self.master.winfo_x()
        main_y = self.master.winfo_y()

        popup_width = 500
        popup_height = 500

        # Calculate popup position
        center_x = main_x + (main_width // 2) - (popup_width // 2)
        center_y = main_y + (main_height // 2) - (popup_height // 2)

        screen_width = self.master.winfo_screenwidth()
        screen_height = self.master.winfo_screenheight()
        center_x = max(0, min(center_x, screen_width - popup_width))
        center_y = max(0, min(center_y, screen_height - popup_height))


        # Apply the calculated geometry (size + position)
        popup.geometry(f'{popup_width}x{popup_height}+{center_x}+{center_y}')
        # --- End Calculate center position ---


        popup.transient(self.master) # Stay on top of main window
        popup.grab_set() # Modal window

        # Ensure popup is removed if closed via window manager
        popup.protocol("WM_DELETE_WINDOW", popup.destroy)


        # Summary Info Frame
        summary_frame = ttk.LabelFrame(popup, text=self.translate("cache_info_frame_title")) # Use translated text
        summary_frame.pack(pady=10, padx=10, fill="x")
        ttk.Label(summary_frame, text=self.translate("cache_directory_label", cache_path)).pack(anchor="w", padx=5, pady=2) # Use translated text
        self.cache_count_label = ttk.Label(summary_frame, text=self.translate("cache_file_count_label", len(cache_files))) # Use translated text
        self.cache_count_label.pack(anchor="w", padx=5, pady=2)
        # Store total_size_mb for potential updates if files are deleted
        self.total_size_mb_cache_dialog = total_size_mb
        self.cache_size_label = ttk.Label(summary_frame, text=self.translate("cache_total_size_label", self.total_size_mb_cache_dialog)) # Use translated text
        self.cache_size_label.pack(anchor="w", padx=5, pady=2)

        # File List Frame
        list_frame = ttk.LabelFrame(popup, text=self.translate("cache_files_frame_title")) # Use translated text
        list_frame.pack(pady=5, padx=10, fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Use Listbox with extended selection
        self.cache_listbox = Listbox(list_frame, selectmode=tk.EXTENDED, yscrollcommand=scrollbar.set)
        for display_text, _ in cache_info_list:
            self.cache_listbox.insert(tk.END, display_text)
        self.cache_listbox.pack(side=tk.LEFT, fill="both", expand=True)
        scrollbar.config(command=self.cache_listbox.yview)

        # Map display text index back to file path - use a list mirroring the listbox
        self._cache_file_paths = [file_path for _, file_path in cache_info_list]


        # Select All checkbox
        select_all_var = tk.BooleanVar(value=False)
        def toggle_select_all():
            if select_all_var.get():
                self.cache_listbox.select_set(0, tk.END)
            else:
                self.cache_listbox.select_clear(0, tk.END)
        # Store checkbox reference
        self.cache_select_all_cb = ttk.Checkbutton(list_frame, text=self.translate("cache_select_all_checkbox"), variable=select_all_var, command=toggle_select_all) # Use translated text
        self.cache_select_all_cb.pack(pady=(5,0), anchor="w")


        # Button Frame
        button_frame = ttk.Frame(popup)
        button_frame.pack(pady=10)

        def delete_selected_cache_files():
            selected_indices = self.cache_listbox.curselection()
            if not selected_indices:
                messagebox.showinfo(self.translate("cache_no_selection_title"), self.translate("cache_no_selection_text"), parent=popup) # Use translated text
                return

            files_to_delete = [self._cache_file_paths[i] for i in selected_indices]

            confirm = messagebox.askyesno(self.translate("cache_confirm_delete_title"),
                                        self.translate("cache_confirm_delete_text", len(files_to_delete)), # Use translated text
                                        parent=popup)
            if confirm:
                deleted_count = 0
                error_occurred = False
                # print(f"DEBUG: Attempting to delete files: {files_to_delete}")
                for file_path in files_to_delete:
                    if os.path.exists(file_path) and os.path.isfile(file_path):
                        try:
                            os.unlink(file_path)
                            deleted_count += 1
                            # print(f"DEBUG: Deleted file: {file_path}")
                            # Also remove from in-memory cache if it corresponds to a console ID
                            try:
                                filename = os.path.basename(file_path)
                                if filename.startswith("console_") and filename.endswith(".json"):
                                     console_id = filename.replace("console_", "").replace(".json", "")
                                     if console_id in self.cached_data:
                                         del self.cached_data[console_id]
                                         # print(f"DEBUG: Removed console_id {console_id} from in-memory cache.")
                            except Exception as e_internal:
                                 print(self.translate("cache_warn_in_memory_delete", file_path, e_internal)) # Use translated text
                                 pass # Ignore errors removing from in-memory cache


                        except Exception as e:
                            print(self.translate("cache_delete_error", file_path, e)) # Use translated text
                            self.status_bar_text_var.set(self.translate("status_cache_delete_error", os.path.basename(file_path))) # Use translated text
                            error_occurred = True # Mark that an error occurred
                    # else:
                         # print(f"WARN: File not found during deletion attempt (already gone?): {file_path}")


                # Update the listbox and info after deletion
                self.update_cache_manager_dialog_content(popup, select_all_var) # Pass select_all_var to reset it

                # Show final message
                if error_occurred:
                     messagebox.showwarning(self.translate("cache_delete_error_warning_title"), self.translate("cache_delete_error_warning_text", deleted_count), parent=popup) # Use translated text
                else:
                     messagebox.showinfo(self.translate("cache_delete_success_title"), self.translate("cache_delete_success_text", deleted_count), parent=popup) # Use translated text

                self.on_selection_change(None) # Update button states in main window

        # Store button references
        self.cache_delete_selected_button_ref = ttk.Button(button_frame, text=self.translate("cache_delete_selected_button"), command=delete_selected_cache_files) # Use translated text
        self.cache_delete_selected_button_ref.pack(side=tk.LEFT, padx=5)
        self.cache_close_button_ref = ttk.Button(button_frame, text=self.translate("cache_close_button"), command=popup.destroy) # Use translated text
        self.cache_close_button_ref.pack(side=tk.LEFT, padx=5)


    def update_cache_manager_dialog_content(self, popup, select_all_var):
        """Updates the listbox and summary info in the cache manager dialog."""
        # Note: This function only updates the *content* based on current files,
        # not the labels/buttons themselves (that's done in update_ui_language).
        cache_path = self.cache_dir
        cache_files = glob.glob(os.path.join(self.cache_dir, "console_*.json"))
        cache_info_list_recheck = [] # Use a new name to avoid confusion with the initial list
        total_size_mb = 0

        # Gather info again
        if os.path.isdir(cache_path):
            for file_path in cache_files:
                 if os.path.isfile(file_path):
                    filename = os.path.basename(file_path)
                    try:
                        file_size_bytes = os.path.getsize(file_path)
                        file_size_mb = file_size_bytes / (1024 * 1024)
                        total_size_mb += file_size_mb

                        # Try to get console name
                        console_id = filename.replace("console_", "").replace(".json", "")
                        # Ensure console_id is a valid key type (string)
                        console_id_str = str(console_id) if console_id else None
                        console_name = self.console_id_to_name_map.get(console_id_str, f"ID {console_id if console_id else 'N/A'}")

                        display_text = f"{console_name} ({filename} - {file_size_mb:.2f} MB)"
                        cache_info_list_recheck.append((display_text, file_path))
                    except Exception as e:
                        # print(f"Fehler beim Verarbeiten von Cache-Datei {filename} für Anzeige (Update): {e}")
                        try:
                            file_size_bytes = os.path.getsize(file_path)
                            file_size_mb = file_size_bytes / (1024 * 1024)
                            total_size_mb += file_size_mb
                            display_text = f"{filename} ({self.translate('cache_size_label', file_size_mb):s})" # Format string like "Größe: %.2f MB"
                            cache_info_list_recheck.append((display_text, file_path))
                        except:
                            display_text = f"{filename} ({self.translate('cache_unknown_size')})" # Use translated text
                            cache_info_list_recheck.append((display_text, file_path))


        # Sort cache_info_list
        cache_info_list_recheck.sort(key=lambda item: item[0])

        # Update summary labels in the dialog (assuming they exist)
        if hasattr(self, 'cache_count_label') and hasattr(self, 'cache_size_label'):
             self.cache_count_label.config(text=self.translate("cache_file_count_label", len(cache_files))) # Use translated text
             # Update stored total size and the label
             self.total_size_mb_cache_dialog = total_size_mb
             self.cache_size_label.config(text=self.translate("cache_total_size_label", self.total_size_mb_cache_dialog)) # Use translated text

        # Clear and repopulate the listbox
        if hasattr(self, 'cache_listbox'):
            self.cache_listbox.delete(0, tk.END)
            for display_text, _ in cache_info_list_recheck:
                self.cache_listbox.insert(tk.END, display_text)

        # Update the internal file paths list
        self._cache_file_paths = [file_path for _, file_path in cache_info_list_recheck]

        # Reset Select All checkbox
        if hasattr(self, 'cache_select_all_cb'): # Ensure the checkbox reference exists
             select_all_var.set(False)

    def save_options(self):
        """Saves the current state of the option checkboxes and the extension entry to config."""
        self.save_config()


    def setup_ui(self):
        """Create the complete UI with the new structure"""
        # --- Login Frame ---
        # Store frame references to update language later
        self.login_frame = ttk.LabelFrame(self.master, text="") # Set text later via update_ui_language
        self.login_frame.pack(padx=10, pady=10, fill="x")

        self.username_label = ttk.Label(self.login_frame, text="") # Set text later
        self.username_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.username_entry = ttk.Entry(self.login_frame, textvariable=self.username, width=40)
        self.username_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

        self.api_key_label = ttk.Label(self.login_frame, text="") # Set text later
        self.api_key_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.api_key_entry = ttk.Entry(self.login_frame, textvariable=self.api_key, show="*", width=40)
        self.api_key_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")

        status_frame = ttk.Frame(self.login_frame)
        status_frame.grid(row=2, column=0, columnspan=2, pady=(10,5))
        self.login_status_light = tk.Canvas(status_frame, width=20, height=20, bg="red", highlightthickness=1, highlightbackground="grey")
        self.login_status_light.pack(side=tk.LEFT, padx=(0,5))
        self.login_status_label = ttk.Label(status_frame, text="") # Set text later
        self.login_status_label.pack(side=tk.LEFT)

        button_frame = ttk.Frame(self.login_frame)
        button_frame.grid(row=3, column=0, columnspan=2, pady=(0,5))
        self.login_button = ttk.Button(button_frame, text="", command=self.test_login) # Set text later
        self.login_button.pack(side=tk.LEFT, padx=5)
        self.logout_button = ttk.Button(button_frame, text="", command=self.clear_credentials) # Set text later
        self.logout_button.pack(side=tk.LEFT, padx=5)
        self.login_frame.columnconfigure(1, weight=1)

        # --- System & Datenabruf Kasten ---
        self.system_data_frame = ttk.LabelFrame(self.master, text="") # Set text later
        self.system_data_frame.pack(padx=10, pady=10, fill="x")

        self.select_system_label = ttk.Label(self.system_data_frame, text="") # Set text later
        self.select_system_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.console_dropdown = ttk.Combobox(self.system_data_frame, textvariable=self.selected_console_id_var, state="disabled", width=37)
        self.console_dropdown.grid(row=0, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        self.console_dropdown.bind("<<ComboboxSelected>>", self.on_selection_change)

        # Cache Options Frame - Now with a single "Cache verwalten" button
        cache_options_frame = ttk.Frame(self.system_data_frame)
        cache_options_frame.grid(row=1, column=0, columnspan=3, pady=5, sticky="w")
        self.cache_manager_button = ttk.Button(cache_options_frame, text="", command=self.show_cache_manager_dialog) # Set text later
        self.cache_manager_button.pack(side=tk.LEFT, padx=5) # Single button

        # Data Inclusion Options
        include_options_frame = ttk.Frame(self.system_data_frame)
        include_options_frame.grid(row=2, column=0, columnspan=3, pady=5, sticky="w")
        # Added command to save options when checkbox state changes
        # Store checkbox references
        self.include_achievements_cb = ttk.Checkbutton(include_options_frame, text="", variable=self.include_achievements_var, command=self.save_options) # Set text later
        self.include_achievements_cb.pack(side=tk.LEFT, padx=5)
        self.include_patch_urls_cb = ttk.Checkbutton(include_options_frame, text="", variable=self.include_patch_urls_var, command=self.save_options) # Set text later
        self.include_patch_urls_cb.pack(side=tk.LEFT, padx=5)


        self.fetch_data_button = ttk.Button(self.system_data_frame, text="", command=self.fetch_data, state="disabled") # Set text later
        self.fetch_data_button.grid(row=3, column=0, columnspan=3, pady=10, padx=5)

        self.system_data_frame.columnconfigure(1, weight=1)


        # --- DAT Erstellung Kasten ---
        self.dat_creation_frame = ttk.LabelFrame(self.master, text="") # Set text later
        self.dat_creation_frame.pack(padx=10, pady=10, fill="x")

        self.dat_save_path_label = ttk.Label(self.dat_creation_frame, text="") # Set text later
        self.dat_save_path_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.dat_save_path_entry = ttk.Entry(self.dat_creation_frame, textvariable=self.dat_save_path, state="readonly", width=30)
        self.dat_save_path_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.browse_dat_button = ttk.Button(self.dat_creation_frame, text="", command=self.select_dat_save_path) # Set text later
        self.browse_dat_button.grid(row=0, column=2, padx=5, pady=5)

        self.create_dat_button = ttk.Button(self.dat_creation_frame, text="", command=self.create_dat_file, state="disabled") # Set text later
        self.create_dat_button.grid(row=1, column=0, columnspan=3, pady=10, padx=5)

        self.dat_creation_frame.columnconfigure(1, weight=1)


        # --- Collection Erstellung Kasten (Renamed from RetroPie Collection Kasten) ---
        # Store frame references to update language later
        self.collection_creation_frame = ttk.LabelFrame(self.master, text="") # Set text later via update_ui_language
        # Changed from retropie_collection_frame.pack
        self.collection_creation_frame.pack(padx=10, pady=10, fill="x")

        # RetroPie Path row
        self.retropie_path_label = ttk.Label(self.collection_creation_frame, text="") # Set text later
        self.retropie_path_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.retropie_path_entry = ttk.Entry(self.collection_creation_frame, textvariable=self.retropie_base_path, width=30)
        self.retropie_path_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.browse_retropie_button = ttk.Button(self.collection_creation_frame, text="", command=self.select_retropie_rom_base_path) # Set text later
        self.browse_retropie_button.grid(row=0, column=2, padx=5, pady=5)

        # Add Batocera Path row below RetroPie
        self.batocera_path_label = ttk.Label(self.collection_creation_frame, text="") # Set text later
        # Changed row to 1
        self.batocera_path_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.batocera_path_entry = ttk.Entry(self.collection_creation_frame, textvariable=self.batocera_base_path, width=30)
        # Changed row to 1
        self.batocera_path_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        # New browse button for Batocera
        self.browse_batocera_button = ttk.Button(self.collection_creation_frame, text="", command=self.select_batocera_rom_base_path) # Set text later
        # Changed row to 1
        self.browse_batocera_button.grid(row=1, column=2, padx=5, pady=5)


        # Collection CFG Save Path row (now row 2)
        self.collection_cfg_save_path_label = ttk.Label(self.collection_creation_frame, text="") # Set text later
        # Changed row to 2
        self.collection_cfg_save_path_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.collection_cfg_save_path_entry = ttk.Entry(self.collection_creation_frame, textvariable=self.collection_cfg_save_path, state="readonly", width=30)
        # Changed row to 2
        self.collection_cfg_save_path_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew")
        self.browse_collection_button = ttk.Button(self.collection_creation_frame, text="", command=self.select_collection_cfg_save_path) # Set text later
        self.browse_collection_button.grid(row=2, column=2, padx=5, pady=5)

        # NEW: Rom Extension Entry Field (replacing the checkbox) - now row 3
        self.rom_extension_label = ttk.Label(self.collection_creation_frame, text="") # Set text later via update_ui_language
        self.rom_extension_label.grid(row=3, column=0, padx=5, pady=5, sticky="w")

        self.rom_extension_entry = ttk.Entry(self.collection_creation_frame, textvariable=self.rom_extension_var, width=10)
        self.rom_extension_entry.grid(row=3, column=1, padx=5, pady=5, sticky="w")
        # Bind <FocusOut> to save the configuration when the entry loses focus
        self.rom_extension_entry.bind("<FocusOut>", lambda event: self.save_options())
        # Optionally, bind <Return> as well if you want it saved when hitting Enter
        self.rom_extension_entry.bind("<Return>", lambda event: (self.save_options(), self.master.focus_set())) # Save and shift focus away

        # OLD: Checkbox für gezippte ROMs (now row 3)
        # OLD: Added command to save options when checkbox state changes
        # OLD: Store checkbox reference
        # OLD: self.roms_are_zipped_cb = ttk.Checkbutton(self.collection_creation_frame, text="", variable=self.roms_are_zipped_var, command=self.save_options) # Set text later
        # OLD: Changed row to 3
        # OLD: self.roms_are_zipped_cb.grid(row=3, column=0, columnspan=3, padx=5, pady=5, sticky="w")


        # Button Frame for collection creation buttons (now row 4)
        collection_button_frame = ttk.Frame(self.collection_creation_frame)
        # Changed row to 4
        collection_button_frame.grid(row=4, column=0, columnspan=3, pady=10, padx=5)

        # RetroPie Collection Button (Use the new variable)
        self.create_retropie_collection_button = ttk.Button(collection_button_frame, text="", command=self.create_retropie_collection, state="disabled") # Set text later
        self.create_retropie_collection_button.pack(side=tk.LEFT, padx=5) # Pack side left

        # Add Batocera Collection Button next to it
        self.create_batocera_collection_button = ttk.Button(collection_button_frame, text="", command=self.create_batocera_collection, state="disabled") # Set text later
        self.create_batocera_collection_button.pack(side=tk.LEFT, padx=5) # Pack side left


        # Use the new frame variable
        self.collection_creation_frame.columnconfigure(1, weight=1)


        # --- Status Bar and Language Dropdown ---
        # Create a frame at the bottom to hold the status bar and language dropdown
        bottom_frame = ttk.Frame(self.master)
        bottom_frame.pack(side=tk.BOTTOM, fill="x", pady=(5,0), padx=10) # Added padx for alignment

        # Status Bar (left side of bottom_frame)
        self.status_bar = ttk.Label(bottom_frame, textvariable=self.status_bar_text_var, relief=tk.SUNKEN, anchor="w")
        self.status_bar.pack(side=tk.LEFT, fill="x", expand=True) # Expand to fill available space

        # Language Dropdown (right side of bottom_frame)
        # Use language names for display, store code in variable
        language_names = list(self.available_languages.values())
        current_code = self.selected_language_code_var.get()
        current_name = self.available_languages.get(current_code, current_code) # Get name, fallback to code

        self.language_dropdown = ttk.Combobox(bottom_frame, values=language_names, state="readonly", width=15)
        self.language_dropdown.pack(side=tk.RIGHT, padx=(10, 0)) # Add some padding to the left

        # Set the initial selection to the currently loaded language
        if current_name in language_names:
             self.language_dropdown.set(current_name)
        elif language_names:
             self.language_dropdown.set(language_names[0]) # Fallback to the first language if saved one isn't available
             # If we fell back, update the internal variable as well
             fallback_code = list(self.available_languages.keys())[0]
             if fallback_code != current_code:
                 self.selected_language_code_var.set(fallback_code)
                 # save_config is called later in __init__

        self.language_dropdown.bind("<<ComboboxSelected>>", self.on_language_selected)

        # Configure the bottom frame to give status bar priority in horizontal resizing
        bottom_frame.columnconfigure(0, weight=1)

        # NEW: About Button (placed next to language dropdown for now, adjust as needed)
        self.about_button = ttk.Button(bottom_frame, text="", command=self.show_about_dialog) # Set text later via update_ui_language
        self.about_button.pack(side=tk.RIGHT, padx=(10, 0)) # Pack to the right of status bar, left of language dropdown


    def on_language_selected(self, event):
        """Handles language selection from the dropdown."""
        selected_name = self.language_dropdown.get()
        # Find the corresponding language code
        selected_code = None
        for code, name in self.available_languages.items():
            if name == selected_name:
                selected_code = code
                break

        if selected_code and selected_code != self.selected_language_code_var.get():
            print(f"DEBUG: Language selected: {selected_name} ({selected_code})")
            self.selected_language_code_var.set(selected_code) # Update the variable first
            self.load_language(selected_code) # Load the new language
            self.update_ui_language() # Update all UI elements
            self.save_config() # Save the selected language to config
            # self.status_bar_text_var.set(self.translate("status_ready")) # Reset status bar text - handled by subsequent logic)

    def show_about_dialog(self):
        """Shows an 'About' dialog with version, author, and thanks."""
        self._about_popup = tk.Toplevel(self.master)
        popup = self._about_popup
        popup.title(self.translate("about_dialog_title"))
        popup.resizable(False, False)

        # Calculate center position relative to the main window
        self.master.update_idletasks()
        main_width = self.master.winfo_width()
        main_height = self.master.winfo_height()
        main_x = self.master.winfo_x()
        main_y = self.master.winfo_y()

        popup_width = 350
        popup_height = 200

        center_x = main_x + (main_width // 2) - (popup_width // 2)
        center_y = main_y + (main_height // 2) - (popup_height // 2)

        screen_width = self.master.winfo_screenwidth()
        screen_height = self.master.winfo_screenheight()
        center_x = max(0, min(center_x, screen_width - popup_width))
        center_y = max(0, min(center_y, screen_height - popup_height))

        popup.geometry(f'{popup_width}x{popup_height}+{center_x}+{center_y}')

        popup.transient(self.master)
        popup.grab_set()
        popup.protocol("WM_DELETE_WINDOW", popup.destroy)

        # Content of the About dialog
        # Store references for language updates
        self._about_label_version = ttk.Label(popup, text=self.translate("about_version_text", "1.0"))
        self._about_label_version.pack(pady=10)

        self._about_label_author = ttk.Label(popup, text=self.translate("about_author_text", "3Draco"))
        self._about_label_author.pack(pady=5)

        self._about_label_thanks = ttk.Label(popup, text=self.translate("about_thanks_text"))
        self._about_label_thanks.pack(pady=5)

        ttk.Button(popup, text=self.translate("about_close_button"), command=popup.destroy).pack(pady=10)

    def select_retropie_rom_base_path(self):
        """Select the base path for RetroPie roms and save to config"""
        # Use the current value if it looks like a path, otherwise start in home or root
        current_path = self.retropie_base_path.get()
        initial_dir = current_path if current_path and os.path.isdir(os.path.dirname(current_path) if os.path.isfile(current_path) else current_path) else os.path.expanduser("~") if os.path.isdir(os.path.expanduser("~")) else "/"

        path = filedialog.askdirectory(title=self.translate("retropie_rom_path_label"), initialdir=initial_dir) # Use translated text
        if path:
            norm_path = os.path.normpath(path)
            self.retropie_base_path.set(norm_path)
            self.save_config() # Save path immediately
            # Updated status key
            self.status_bar_text_var.set(self.translate("status_retropie_rom_path_selected", norm_path))
        else:
            # Don't clear path if dialog is cancelled, user might want to keep the old one
            # Updated status key
            self.status_bar_text_var.set(self.translate("status_retropie_rom_path_not_selected"))
        self.on_selection_change(None)


    def select_batocera_rom_base_path(self):
        """Select the base path for Batocera roms and save to config"""
        current_path = self.batocera_base_path.get()
        # Start in the default Batocera path if the current path is empty or doesn't look like a dir
        initial_dir = current_path if current_path and os.path.isdir(os.path.dirname(current_path) if os.path.isfile(current_path) else current_path) else "/userdata/roms" if os.path.isdir("/userdata/roms") else "/"


        # Use the new translation key for the title
        path = filedialog.askdirectory(title=self.translate("batocera_rom_path_label"), initialdir=initial_dir)
        if path:
            norm_path = os.path.normpath(path)
            self.batocera_base_path.set(norm_path)
            self.save_config() # Save path immediately
            # Add new status key
            self.status_bar_text_var.set(self.translate("status_batocera_rom_path_selected", norm_path))
        else:
            # Add new status key
            self.status_bar_text_var.set(self.translate("status_batocera_rom_path_not_selected"))
        self.on_selection_change(None)


    def select_dat_save_path(self):
        """Select directory to save DAT files and save to config"""
        current_path = self.dat_save_path.get()
        initial_dir = current_path if current_path and os.path.isdir(current_path) else self.script_dir # Use current path if it's a dir, otherwise script dir

        path = filedialog.askdirectory(title=self.translate("dat_save_location_label"), initialdir=initial_dir) # Use translated text
        if path:
            norm_path = os.path.normpath(path)
            self.dat_save_path.set(norm_path)
            self.save_config() # Save path immediately
            self.status_bar_text_var.set(self.translate("status_dat_save_location", norm_path)) # Use translated text
        else:
            # Don't clear path if dialog is cancelled, user might want to keep the old one
            self.status_bar_text_var.set(self.translate("status_dat_save_location_not_selected")) # Use translated text
        self.on_selection_change(None)


    def select_collection_cfg_save_path(self):
        """Select directory to save RetroPie Collection (.cfg) files and save to config"""
        current_path = self.collection_cfg_save_path.get()
        initial_dir = current_path if current_path and os.path.isdir(current_path) else self.script_dir # Use current path if it's a dir, otherwise script dir

        path = filedialog.askdirectory(title=self.translate("collection_cfg_save_path_label"), initialdir=initial_dir) # Use translated text
        if path:
            norm_path = os.path.normpath(path)
            self.collection_cfg_save_path.set(norm_path)
            self.save_config() # Save path immediately
            self.status_bar_text_var.set(self.translate("status_collection_cfg_save_location", norm_path)) # Use translated text
        else:
            # Don't clear path if dialog is cancelled, user might want to keep the old one
            self.status_bar_text_var.set(self.translate("status_collection_cfg_save_location_not_selected")) # Use translated text
        self.on_selection_change(None)


    def _make_api_request(self, url, params=None, authenticate=True, max_retries_on_429=4, initial_backoff_s=3):
        """Makes API request with authentication, error handling, and 429 retry logic."""
        if params is None:
            params = {}

        if authenticate:
            user = self.username.get()
            key = self.api_key.get()
            if not user or not key:
                messagebox.showerror(self.translate("auth_error_message_title"), self.translate("auth_error_message_text")) # Use translated text
                self.status_bar_text_var.set(self.translate("status_auth_failed_missing")) # Use translated text
                return None

            params['z'] = user
            params['y'] = key

        retries = 0
        current_url_for_msg = url.split('?')[0]

        while retries <= max_retries_on_429:
            try:
                status_msg = self.translate("status_requesting_api", os.path.basename(current_url_for_msg)) # Use translated text
                if retries > 0:
                    status_msg += f" ({self.translate('api_rate_limit_wait_progress', retries + 1, max_retries_on_429+1).split('(')[-1].strip()}" # Extract retry part
                status_msg += "..."
                self.status_bar_text_var.set(status_msg)
                self.master.update_idletasks()

                print(f"API Request to {url} with params: {params}")
                with requests.Session() as s:
                    response = s.get(url, params=params, timeout=60)
                print(f"Response status: {response.status_code} from {response.url}")
                # print(f"Response text (first 200 chars): {response.text[:200]}...") # Optional: can be very verbose
                response.raise_for_status()
                try:
                    return response.json()
                except json.JSONDecodeError as json_err:
                    self.status_bar_text_var.set(self.translate("api_parsing_error", os.path.basename(current_url_for_msg))) # Use translated text
                    messagebox.showerror(self.translate("api_error_message_json_title"), self.translate("api_error_message_json_text", url, json_err, response.text[:200])) # Use translated text
                    return None

            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429: # Too Many Requests
                    retries += 1
                    if retries <= max_retries_on_429:
                        wait_time = initial_backoff_s * (2 ** (retries - 1))
                        retry_after = e.response.headers.get('Retry-After')
                        if retry_after:
                            try:
                                wait_time = int(retry_after) + 1 # Add 1 second buffer
                                print(f"API Rate Limit (429) hit. Server requested wait for {retry_after}s. Retrying in {wait_time}s.")
                            except ValueError:
                                print(f"API Rate Limit (429) hit. No valid Retry-After header. Retrying in {wait_time}s (exponential backoff).")
                        else:
                            print(f"API Rate Limit (429) hit. Retrying in {wait_time}s (exponential backoff).")

                        self.status_bar_text_var.set(self.translate("api_rate_limit_wait", wait_time)) # Use translated text
                        self.master.update_idletasks()
                        time.sleep(wait_time)
                        continue # Continue to the next attempt
                    else:
                        self.status_bar_text_var.set(self.translate("status_api_rate_limit_exceeded")) # Use translated text
                        messagebox.showerror(self.translate("api_error_message_rate_limit_title"), self.translate("api_error_message_rate_limit_text", url, e.response.status_code)) # Use translated text
                        return None
                elif e.response.status_code == 401: # Unauthorized
                    self.status_bar_text_var.set(self.translate("status_auth_failed_credentials")) # Use translated text
                    messagebox.showerror(self.translate("auth_error_message_failed_title"), self.translate("auth_error_message_failed_text")) # Use translated text
                    self.login_status_light.config(bg="red")
                    self.login_status_label.config(text=self.translate("status_disconnected")) # Use translated text
                    return None
                else: # Other HTTP errors
                    self.status_bar_text_var.set(self.translate("status_api_error_http", e.response.status_code)) # Use translated text
                    messagebox.showerror(self.translate("api_error_message_http_title"), self.translate("api_error_message_http_text", url, e.response.status_code, e.response.text[:200])) # Use translated text
                    return None
            except requests.exceptions.ConnectionError as e:
                self.status_bar_text_var.set(self.translate("status_network_error")) # Use translated text
                messagebox.showerror(self.translate("network_error_title"), self.translate("network_error_text", str(e))) # Use translated text
                return None
            except requests.exceptions.Timeout:
                self.status_bar_text_var.set(self.translate("status_timeout_error")) # Use translated text
                messagebox.showerror(self.translate("timeout_error_title"), self.translate("timeout_error_text", url)) # Use translated text
                return None
            except Exception as e:
                self.status_bar_text_var.set(self.translate("status_request_failed_general", str(e))) # Use translated text
                messagebox.showerror(self.translate("general_error_title"), self.translate("general_error_text", str(e))) # Use translated text
                return None
        return None # Should not be reached if retries are handled, but as a safeguard

    def test_login(self):
        """Tests the provided username and API key."""
        user = self.username.get()
        key = self.api_key.get()

        if not user or not key:
            self.status_bar_text_var.set(self.translate("status_auth_failed_input_missing")) # Use translated text
            messagebox.showwarning(self.translate("login_failed_input_missing_title"), self.translate("login_failed_input_missing_text")) # Use translated text
            self.login_status_light.config(bg="red")
            self.login_status_label.config(text=self.translate("status_disconnected")) # Use translated text
            return

        self.status_bar_text_var.set(self.translate("status_testing_credentials")) # Use translated text
        self.master.update_idletasks() # Update UI to show status

        # Use _make_api_request with a simple endpoint to test credentials
        params = {"z": user, "y": key}
        response = self._make_api_request(API_USER_PROFILE_URL, params=params, authenticate=False) # Authenticate=False because we add params manually

        if response:
            if isinstance(response, dict) and "Username" in response:
                self.login_status_light.config(bg="green")
                self.login_status_label.config(text=self.translate("status_connected")) # Use translated text
                self.status_bar_text_var.set(self.translate("status_login_successful", response["Username"])) # Use translated text
                self.save_config() # Save credentials if successful
                self.load_console_ids() # Load console IDs on successful login
                self.console_dropdown.config(state="readonly") # Enable dropdown
                self.on_selection_change(None) # Update button states
            else:
                # This case should ideally be caught by _make_api_request's JSON error or HTTP error,
                # but as a fallback for unexpected API responses.
                self.login_status_light.config(bg="red")
                self.login_status_label.config(text=self.translate("status_disconnected")) # Use translated text
                self.status_bar_text_var.set(self.translate("status_login_failed_unexpected")) # Use translated text
                messagebox.showerror(self.translate("login_failed_title"), self.translate("login_failed_unexpected_response")) # Use translated text
        else:
            # Error already reported by _make_api_request
            self.login_status_light.config(bg="red")
            self.login_status_label.config(text=self.translate("status_disconnected")) # Use translated text
            # The status bar will already have an error message from _make_api_request

    def load_console_ids(self):
        """Loads console IDs and names from RetroAchievements API."""
        self.status_bar_text_var.set(self.translate("status_fetching_consoles")) # Use translated text
        self.master.update_idletasks()
        try:
            consoles_data = self._make_api_request(API_CONSOLE_IDS_URL, authenticate=True)

            if consoles_data:
                self.console_id_to_name_map = {str(c['ID']): c['Name'] for c in consoles_data}
                self.console_name_to_id_map = {c['Name']: str(c['ID']) for c in consoles_data}
                sorted_console_names = sorted(self.console_name_to_id_map.keys())
                self.console_dropdown['values'] = sorted_console_names
                self.status_bar_text_var.set(self.translate("status_consoles_loaded")) # Use translated text
            else:
                self.status_bar_text_var.set(self.translate("status_consoles_load_failed")) # Use translated text
                self.console_dropdown['values'] = [] # Clear dropdown
                self.console_dropdown.config(state="disabled") # Disable dropdown on failure
        except Exception as e:
            self.status_bar_text_var.set(self.translate("status_error_loading_consoles", str(e))) # Use translated text
            self.console_dropdown['values'] = []
            self.console_dropdown.config(state="disabled")

    def on_selection_change(self, event):
        """Enables/disables buttons based on console selection and login status."""
        selected_console_name = self.selected_console_id_var.get()
        console_id = self.console_name_to_id_map.get(selected_console_name)

        is_logged_in = (self.login_status_light.cget("bg") == "green")
        is_console_selected = bool(console_id)

        # Enable/Disable Fetch Data button
        if is_logged_in and is_console_selected:
            self.fetch_data_button.config(state="normal")
        else:
            self.fetch_data_button.config(state="disabled")
            self.create_dat_button.config(state="disabled")
            self.create_retropie_collection_button.config(state="disabled")
            self.create_batocera_collection_button.config(state="disabled")
            return # Exit if not ready to avoid errors with non-existent data

        # Check if data for the selected console is already in cached_data
        # This will determine if DAT/Collection buttons can be enabled immediately
        if console_id in self.cached_data and self.cached_data[console_id]:
            self.create_dat_button.config(state="normal")
            self.create_retropie_collection_button.config(state="normal")
            self.create_batocera_collection_button.config(state="normal")
            self.status_bar_text_var.set(self.translate("status_ready")) # Reset status bar if data is loaded
        else:
            self.create_dat_button.config(state="disabled")
            self.create_retropie_collection_button.config(state="disabled")
            self.create_batocera_collection_button.config(state="disabled")
            if is_logged_in and is_console_selected:
                 self.status_bar_text_var.set(self.translate("status_fetch_needed"))


    def fetch_data(self):
        """Starts the data fetching process in a separate thread."""
        selected_console_name = self.selected_console_id_var.get()
        selected_console_id = self.console_name_to_id_map.get(selected_console_name)

        if not selected_console_id:
            messagebox.showwarning(self.translate("no_system_selected_title"), self.translate("no_system_selected_text")) # Use translated text
            self.status_bar_text_var.set(self.translate("status_no_system_selected")) # Use translated text
            return

        # Disable buttons to prevent multiple fetches
        self.fetch_data_button.config(state="disabled")
        self.create_dat_button.config(state="disabled")
        self.create_retropie_collection_button.config(state="disabled")
        self.create_batocera_collection_button.config(state="disabled")
        self.console_dropdown.config(state="disabled")
        self.cache_manager_button.config(state="disabled")
        self.logout_button.config(state="disabled") # Prevent logout during fetch

        # Show progress window
        self._show_progress_popup("fetch")

        # Start fetching in a new thread
        self._fetch_worker_thread = threading.Thread(target=self._fetch_data_worker, args=(selected_console_id, selected_console_name))
        self._fetch_worker_thread.start()

    def _fetch_data_worker(self, console_id, console_name):
        """Worker function for fetching data in a separate thread."""
        try:
            self.fetch_progress_label_var.set(self.translate("data_fetch_loading_cache", console_name)) # Use translated text
            self.master.update_idletasks()
            cached_data = self.load_from_cache(console_id)
            if cached_data:
                self.cached_data[console_id] = cached_data
                self.fetch_progress_label_var.set(self.translate("data_fetch_loaded_cache", console_name)) # Use translated text
                # Allow a brief moment to see the "loaded from cache" message
                time.sleep(1)
                self.master.after(0, self._on_fetch_complete, True, console_id) # True for success
                return

            self.fetch_progress_label_var.set(self.translate("data_fetch_requesting_game_list", console_name)) # Use translated text
            self.status_bar_text_var.set(self.translate("status_fetching_games", console_name)) # Use translated text
            self.master.update_idletasks()

            game_list_params = {"i": console_id}
            game_list = self._make_api_request(API_GAME_LIST_URL, params=game_list_params, authenticate=True)

            if not game_list or not isinstance(game_list, list):
                self.fetch_progress_label_var.set(self.translate("data_fetch_failed_game_list")) # Use translated text
                self.master.after(0, self._on_fetch_complete, False, console_id)
                return

            # Filter out entries where Hash is '00000000000000000000000000000000'
            filtered_game_list = [game for game in game_list if game.get("Hash") != '00000000000000000000000000000000']
            total_games = len(filtered_game_list)
            self.fetch_progress_label_var.set(self.translate("data_fetch_fetching_details", 0, total_games)) # Use translated text
            self.master.update_idletasks()

            fetched_games_details = []
            for i, game in enumerate(filtered_game_list):
                game_id = game.get("ID")
                if not game_id:
                    continue

                self.fetch_progress_label_var.set(self.translate("data_fetch_fetching_details_progress", i + 1, total_games, game.get("Title", "Unknown"))) # Use translated text
                self.master.update_idletasks()

                game_details_params = {"i": game_id}
                game_details = self._make_api_request(API_GET_GAME_EXTENDED_URL, params=game_details_params, authenticate=True)

                if game_details:
                    # Merge game list data with extended details
                    merged_data = {**game, **game_details}
                    fetched_games_details.append(merged_data)
                # else: Error already reported by _make_api_request

            # Filter out any games that ended up with the zero hash after fetching details
            final_game_data = [game for game in fetched_games_details if game.get("Hash") != '00000000000000000000000000000000']

            self.cached_data[console_id] = final_game_data # Store in-memory cache
            self.save_to_cache(console_id, final_game_data) # Save to file cache

            self.master.after(0, self._on_fetch_complete, True, console_id) # True for success

        except Exception as e:
            print(f"Error during fetch: {e}")
            self.fetch_progress_label_var.set(self.translate("data_fetch_failed_general", str(e))) # Use translated text
            self.master.after(0, self._on_fetch_complete, False, console_id)

    def _on_fetch_complete(self, success, console_id):
        """Called when data fetching is complete (from the worker thread)."""
        # Ensure progress window is closed
        if self._fetch_progress_popup:
            self._fetch_progress_popup.destroy()
            self._fetch_progress_popup = None

        # Re-enable UI elements
        self.fetch_data_button.config(state="normal")
        self.console_dropdown.config(state="readonly")
        self.cache_manager_button.config(state="normal")
        self.logout_button.config(state="normal")

        if success:
            selected_console_name = self.selected_console_id_var.get()
            self.status_bar_text_var.set(self.translate("status_data_fetch_complete", selected_console_name, len(self.cached_data.get(console_id, [])))) # Use translated text
        else:
            # Status bar already updated by _make_api_request or fetch_data_worker in case of error
            pass # No specific status message needed here beyond what was already set

        # Always update button states after fetch attempt
        self.on_selection_change(None) # This will enable DAT/Collection buttons if data is available

    def _show_progress_popup(self, type):
        """Shows a generic progress popup window."""
        if type == "fetch":
            if self._fetch_progress_popup and tk.Toplevel.winfo_exists(self._fetch_progress_popup):
                return # Already open
            self._fetch_progress_popup = tk.Toplevel(self.master)
            popup = self._fetch_progress_popup
            popup.title(self.translate("data_fetch_progress_title")) # Use translated text
            self.fetch_progress_label = ttk.Label(popup, textvariable=self.fetch_progress_label_var)
            self.fetch_progress_label.pack(padx=20, pady=20)
        elif type == "dat":
            if self._dat_progress_popup and tk.Toplevel.winfo_exists(self._dat_progress_popup):
                return # Already open
            self._dat_progress_popup = tk.Toplevel(self.master)
            popup = self._dat_progress_popup
            popup.title(self.translate("dat_creation_progress_title")) # Use translated text
            self.dat_progress_label = ttk.Label(popup, textvariable=self.dat_progress_label_var)
            self.dat_progress_label.pack(padx=20, pady=20)
        elif type == "collection":
            if self._collection_progress_popup and tk.Toplevel.winfo_exists(self._collection_progress_popup):
                return # Already open
            self._collection_progress_popup = tk.Toplevel(self.master)
            popup = self._collection_progress_popup
            popup.title(self.translate("collection_creation_progress_title")) # Use translated text
            self.collection_progress_label = ttk.Label(popup, textvariable=self.collection_progress_label_var)
            self.collection_progress_label.pack(padx=20, pady=20)
        else:
            return

        popup.transient(self.master) # Make it appear on top of the main window
        popup.grab_set() # Make it modal (user must interact with it)
        # Disable closing with window manager X button
        popup.protocol("WM_DELETE_WINDOW", lambda: None)
        popup.geometry("350x100") # Fixed size for progress popups

        # Center the popup on the main window
        self.master.update_idletasks()
        main_x = self.master.winfo_x()
        main_y = self.master.winfo_y()
        main_width = self.master.winfo_width()
        main_height = self.master.winfo_height()

        popup_width = 350
        popup_height = 100

        center_x = main_x + (main_width // 2) - (popup_width // 2)
        center_y = main_y + (main_height // 2) - (popup_height // 2)

        screen_width = self.master.winfo_screenwidth()
        screen_height = self.master.winfo_screenheight()
        center_x = max(0, min(center_x, screen_width - popup_width))
        center_y = max(0, min(center_y, screen_height - popup_height))

        popup.geometry(f'{popup_width}x{popup_height}+{center_x}+{center_y}')
        popup.update_idletasks() # Ensure it's drawn


    def create_dat_file(self):
        """Initiates the DAT file creation process in a separate thread."""
        selected_console_name = self.selected_console_id_var.get()
        selected_console_id = self.console_name_to_id_map.get(selected_console_name)

        if not selected_console_id or selected_console_id not in self.cached_data or not self.cached_data[selected_console_id]:
            messagebox.showwarning(self.translate("no_data_for_dat_title"), self.translate("no_data_for_dat_text")) # Use translated text
            self.status_bar_text_var.set(self.translate("status_no_data_for_dat")) # Use translated text
            return

        save_dir = self.dat_save_path.get()
        if not save_dir:
            messagebox.showwarning(self.translate("no_dat_save_path_title"), self.translate("no_dat_save_path_text")) # Use translated text
            self.status_bar_text_var.set(self.translate("status_no_dat_save_path")) # Use translated text
            return

        # Disable buttons
        self.fetch_data_button.config(state="disabled")
        self.create_dat_button.config(state="disabled")
        self.create_retropie_collection_button.config(state="disabled")
        self.create_batocera_collection_button.config(state="disabled")
        self.console_dropdown.config(state="disabled")
        self.cache_manager_button.config(state="disabled")
        self.logout_button.config(state="disabled")

        # Show progress window
        self._show_progress_popup("dat")

        # Start DAT creation in a new thread
        threading.Thread(target=self._create_dat_file_worker, args=(selected_console_id, selected_console_name, save_dir)).start()

    def _create_dat_file_worker(self, console_id, console_name, save_dir):
        """Worker function for creating DAT file."""
        try:
            game_data = self.cached_data.get(console_id, [])
            total_games = len(game_data)
            dat_file_name = f"RetroAchievements - {console_name}.dat"
            full_path = os.path.join(save_dir, dat_file_name)

            self.dat_progress_label_var.set(self.translate("dat_creation_starting", console_name)) # Use translated text
            self.status_bar_text_var.set(self.translate("status_dat_creating", console_name)) # Use translated text
            self.master.update_idletasks()

            dat_content = []
            dat_content.append(f"""clrmamepro (
    name "{console_name} (RetroAchievements)"
    description "{console_name} (RetroAchievements)"
    version "{datetime.now().strftime('%Y-%m-%d %H-%M-%S')}"
    comment "DAT file generated by RADATool by 3Draco"
    homepage "https://github.com/3Draco/RADATool"
    url "https://retroachievements.org"
    romof ""
)
""")

            for i, game in enumerate(game_data):
                self.dat_progress_label_var.set(self.translate("dat_creation_progress", i + 1, total_games, game.get("Title", "Unknown"))) # Use translated text
                self.master.update_idletasks()

                game_id = game.get("ID", "N/A")
                title = game.get("Title", "Unknown Title")
                hash_value = game.get("Hash", "N/A")
                forum_topic = game.get("ForumTopicID", "N/A")
                console_id_game = game.get("ConsoleID", "N/A")
                developer = game.get("Developer", "N/A")
                publisher = game.get("Publisher", "N/A")
                genre = game.get("Genre", "N/A")
                release_date = game.get("Released", "N/A") # This is likely 'yyyy-mm-dd hh:mm:ss' or 'yyyy-mm-dd'

                # Clean up hash value to ensure it's a valid hex string for SHA1
                if hash_value and len(hash_value) == 32: # MD5
                     # MD5 hash, not SHA1. CLRMAMEPRO expects SHA1 for 'rom' entries.
                     # If RetroAchievements provides only MD5, we can put it as a comment or skip.
                     # For now, will place as a comment if not SHA1 (which is 40 chars)
                     rom_line = f'        rom name="{title}.zip" size="0" crc="00000000" md5="{hash_value}" sha1="0000000000000000000000000000000000000000"'
                elif hash_value and len(hash_value) == 40: # SHA1
                    rom_line = f'        rom name="{title}.zip" size="0" crc="00000000" sha1="{hash_value}"'
                else:
                    rom_line = f'        rom name="{title}.zip" size="0" crc="00000000" sha1="0000000000000000000000000000000000000000"' # Fallback zero hash


                # Construct the game entry
                game_entry = f"""
    game (
        name "{title}"
        description "{title}"
        source "RetroAchievements"
        comment "ID: {game_id}, Forum: {forum_topic}, Console ID: {console_id_game}, Developer: {developer}, Publisher: {publisher}, Genre: {genre}, Released: {release_date}"
{rom_line}
"""

                # Include achievements if requested
                if self.include_achievements_var.get() and "Achievements" in game and game["Achievements"]:
                    achievements_comments = []
                    for ach_id, achievement in game["Achievements"].items():
                        # Only include official achievements
                        if achievement.get('IsOfficial', '0') == '1': # API returns '1' or '0' as strings
                             achievements_comments.append(f"            Achievement: {achievement.get('Title', 'N/A')} ({achievement.get('Points', '0')} pts) - {achievement.get('Description', 'N/A')}")
                    if achievements_comments:
                         game_entry += "        comment \"Achievements:\n" + "\n".join(achievements_comments) + "\"\n"


                # Include patch URLs if requested
                if self.include_patch_urls_var.get() and "Patch" in game and game["Patch"]:
                    patch_url = game["Patch"].get("URL", "N/A")
                    if patch_url != "N/A":
                        game_entry += f'        comment "Patch URL: {patch_url}"\n'


                game_entry += "    )\n"
                dat_content.append(game_entry)

            with open(full_path, 'w', encoding='utf-8') as f:
                f.writelines(dat_content)

            self.master.after(0, self._on_dat_creation_complete, True, full_path)

        except Exception as e:
            print(f"Error during DAT creation: {e}")
            self.master.after(0, self._on_dat_creation_complete, False, None, str(e))

    def _on_dat_creation_complete(self, success, file_path=None, error_message=None):
        """Called when DAT creation is complete (from the worker thread)."""
        if self._dat_progress_popup:
            self._dat_progress_popup.destroy()
            self._dat_progress_popup = None

        # Re-enable buttons
        self.fetch_data_button.config(state="normal")
        self.create_dat_button.config(state="normal")
        self.create_retropie_collection_button.config(state="normal")
        self.create_batocera_collection_button.config(state="normal")
        self.console_dropdown.config(state="readonly")
        self.cache_manager_button.config(state="normal")
        self.logout_button.config(state="normal")

        if success:
            self.status_bar_text_var.set(self.translate("status_dat_creation_complete", file_path)) # Use translated text
            messagebox.showinfo(self.translate("dat_success_title"), self.translate("dat_success_text", file_path)) # Use translated text
        else:
            self.status_bar_text_var.set(self.translate("status_dat_creation_failed", error_message)) # Use translated text
            messagebox.showerror(self.translate("dat_error_title"), self.translate("dat_error_text", error_message)) # Use translated text

        self.on_selection_change(None) # Ensure buttons are in correct state

    def create_retropie_collection(self):
        """Initiates the RetroPie collection file creation process in a separate thread."""
        self._create_collection(system_type="retropie")

    def create_batocera_collection(self):
        """Initiates the Batocera collection file creation process in a separate thread."""
        self._create_collection(system_type="batocera")

    def _create_collection(self, system_type):
        """Handles the common logic for creating collection files (RetroPie or Batocera)."""
        selected_console_name = self.selected_console_id_var.get()
        selected_console_id = self.console_name_to_id_map.get(selected_console_name)

        if not selected_console_id or selected_console_id not in self.cached_data or not self.cached_data[selected_console_id]:
            messagebox.showwarning(self.translate("no_data_for_collection_title"), self.translate("no_data_for_collection_text")) # Use translated text
            self.status_bar_text_var.set(self.translate("status_no_data_for_collection")) # Use translated text
            return

        save_dir = self.collection_cfg_save_path.get()
        if not save_dir:
            messagebox.showwarning(self.translate("no_collection_cfg_save_path_title"), self.translate("no_collection_cfg_save_path_text")) # Use translated text
            self.status_bar_text_var.set(self.translate("status_no_collection_cfg_save_path")) # Use translated text
            return

        # Determine base path based on system_type
        if system_type == "retropie":
            rom_base_path = self.retropie_base_path.get()
            collection_file_name = f"RetroAchievements - {selected_console_name}.cfg"
            status_message_key_start = "status_retropie_collection_creating"
            dialog_title_key = "retropie_collection_success_title"
            dialog_text_key = "retropie_collection_success_text"
            error_dialog_title_key = "retropie_collection_error_title"
            error_dialog_text_key = "retropie_collection_error_text"
            if not rom_base_path:
                messagebox.showwarning(self.translate("no_retropie_rom_path_title"), self.translate("no_retropie_rom_path_text"))
                self.status_bar_text_var.set(self.translate("status_no_retropie_rom_path"))
                return
        elif system_type == "batocera":
            rom_base_path = self.batocera_base_path.get()
            collection_file_name = f"RetroAchievements - {selected_console_name}.batocera.cfg" # Distinct name
            status_message_key_start = "status_batocera_collection_creating"
            dialog_title_key = "batocera_collection_success_title"
            dialog_text_key = "batocera_collection_success_text"
            error_dialog_title_key = "batocera_collection_error_title"
            error_dialog_text_key = "batocera_collection_error_text"
            if not rom_base_path:
                messagebox.showwarning(self.translate("no_batocera_rom_path_title"), self.translate("no_batocera_rom_path_text"))
                self.status_bar_text_var.set(self.translate("status_no_batocera_rom_path"))
                return
        else:
            messagebox.showerror("Internal Error", "Unknown system type for collection creation.")
            return


        full_path = os.path.join(save_dir, collection_file_name)

        # Disable buttons
        self.fetch_data_button.config(state="disabled")
        self.create_dat_button.config(state="disabled")
        self.create_retropie_collection_button.config(state="disabled")
        self.create_batocera_collection_button.config(state="disabled")
        self.console_dropdown.config(state="disabled")
        self.cache_manager_button.config(state="disabled")
        self.logout_button.config(state="disabled")

        # Show progress window
        self._show_progress_popup("collection")

        # Start collection creation in a new thread
        threading.Thread(target=self._create_collection_worker,
                         args=(selected_console_id, selected_console_name, save_dir,
                               rom_base_path, system_type,
                               status_message_key_start, dialog_title_key, dialog_text_key,
                               error_dialog_title_key, error_dialog_text_key)).start()

    def _create_collection_worker(self, console_id, console_name, save_dir, rom_base_path, system_type,
                                   status_message_key_start, dialog_title_key, dialog_text_key,
                                   error_dialog_title_key, error_dialog_text_key):
        """Worker function for creating collection files."""
        try:
            game_data = self.cached_data.get(console_id, [])
            total_games = len(game_data)

            # Determine the collection file name based on system_type
            if system_type == "retropie":
                collection_file_name = f"RetroAchievements - {console_name}.cfg"
                rom_path_prefix = rom_base_path
            elif system_type == "batocera":
                collection_file_name = f"RetroAchievements - {console_name}.batocera.cfg"
                rom_path_prefix = rom_base_path
            else:
                # This should not happen due to prior checks, but as a safeguard
                raise ValueError("Invalid system_type provided to _create_collection_worker")


            full_path = os.path.join(save_dir, collection_file_name)
            rom_extension = self.rom_extension_var.get().strip()
            if not rom_extension.startswith("."):
                rom_extension = "." + rom_extension # Ensure it starts with a dot

            self.collection_progress_label_var.set(self.translate("collection_creation_starting", console_name)) # Use generic collection text
            self.status_bar_text_var.set(self.translate(status_message_key_start, console_name)) # Use system-specific status message
            self.master.update_idletasks()

            collection_content = []

            for i, game in enumerate(game_data):
                self.collection_progress_label_var.set(self.translate("collection_creation_progress", i + 1, total_games, game.get("Title", "Unknown"))) # Use generic collection text
                self.master.update_idletasks()

                game_title = game.get("Title", "Unknown Game").replace('"', "'") # Replace quotes for .cfg format
                # Ensure the ROM filename uses the configured extension
                rom_filename = f"{game_title}{rom_extension}" # Use configured extension
                game_path = os.path.join(rom_path_prefix, console_name, rom_filename) # Assumes roms are in system sub-directories

                # Add quotes around game_path as it might contain spaces
                collection_content.append(f'"{game_path}"\n')

            with open(full_path, 'w', encoding='utf-8') as f:
                f.writelines(collection_content)

            self.master.after(0, self._on_collection_creation_complete, True, full_path, dialog_title_key, dialog_text_key)

        except Exception as e:
            print(f"Error during collection creation: {e}")
            self.master.after(0, self._on_collection_creation_complete, False, None, dialog_title_key, error_dialog_text_key, str(e))

    def _on_collection_creation_complete(self, success, file_path=None, dialog_title_key=None, dialog_text_key=None, error_message=None):
        """Called when collection creation is complete (from the worker thread)."""
        if self._collection_progress_popup:
            self._collection_progress_popup.destroy()
            self._collection_progress_popup = None

        # Re-enable buttons
        self.fetch_data_button.config(state="normal")
        self.create_dat_button.config(state="normal")
        self.create_retropie_collection_button.config(state="normal")
        self.create_batocera_collection_button.config(state="normal")
        self.console_dropdown.config(state="readonly")
        self.cache_manager_button.config(state="normal")
        self.logout_button.config(state="normal")

        if success:
            self.status_bar_text_var.set(self.translate("status_collection_creation_complete", file_path)) # Use translated text
            messagebox.showinfo(self.translate(dialog_title_key), self.translate(dialog_text_key, file_path)) # Use translated text
        else:
            self.status_bar_text_var.set(self.translate("status_collection_creation_failed", error_message)) # Use translated text
            # Use error_dialog_text_key for error message
            messagebox.showerror(self.translate(dialog_title_key), self.translate(dialog_text_key, error_message))

        self.on_selection_change(None) # Ensure buttons are in correct state

if __name__ == "__main__":
    root = tk.Tk()
    app = RetroAchievementsDATGenerator(root)
    root.mainloop()