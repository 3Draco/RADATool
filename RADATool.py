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
                self._about_label_version.config(text=self.translate("about_version_text", "1.1"))
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
        cache_info_list = [] # List of (display_text, file_path)
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
        # Changed row to 2
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
                if e.response.status_code == 422:
                    self.status_bar_text_var.set(self.translate("api_error_422", os.path.basename(current_url_for_msg))) # Use translated text
                    try:
                        error_detail = e.response.json()
                    except json.JSONDecodeError:
                        error_detail = e.response.text[:200]
                    messagebox.showerror(self.translate("api_error_message_422_title"),
                                       self.translate("api_error_message_422_text", e.request.url, params, error_detail)) # Use translated text
                    return None
                elif e.response.status_code == 429 and retries < max_retries_on_429:
                    wait_time = initial_backoff_s * (2 ** retries)
                    wait_time += (wait_time * 0.2 * (os.urandom(1)[0]/255.0))
                    wait_time = min(wait_time, 60)
                    self.status_bar_text_var.set(self.translate("api_rate_limit_wait", os.path.basename(current_url_for_msg), wait_time)) # Use translated text

                    # Check if progress popups and their labels exist before updating
                    fetch_popup_active = hasattr(self, '_fetch_progress_popup') and self._fetch_progress_popup and tk.Toplevel.winfo_exists(self._fetch_progress_popup)
                    dat_popup_active = hasattr(self, '_dat_progress_popup') and self._dat_progress_popup and tk.Toplevel.winfo_exists(self._dat_progress_popup)
                    collection_popup_active = hasattr(self, '_collection_progress_popup') and self._collection_progress_popup and tk.Toplevel.winfo_exists(self._collection_progress_popup)

                    wait_start_time = time.monotonic()
                    while time.monotonic() < wait_start_time + wait_time:
                        remaining_slice = (wait_start_time + wait_time) - time.monotonic()
                        sleep_slice = min(0.1, remaining_slice)
                        if sleep_slice <= 0: break
                        time.sleep(sleep_slice)
                        time_left = max(0, wait_time - (time.monotonic() - wait_start_time))
                        wait_msg = self.translate("api_rate_limit_wait_progress", time_left, retries + 1, max_retries_on_429+1) # Use translated text
                        self.status_bar_text_var.set(wait_msg)

                        # Update relevant progress labels if popups are active
                        if fetch_popup_active and self.fetch_progress_label_var is not None:
                            self.fetch_progress_label_var.set(self.translate("api_rate_limit_resume_fetch")) # Use translated text (simplified resume)
                        if dat_popup_active and self.dat_progress_label_var is not None:
                            self.dat_progress_label_var.set(self.translate("api_rate_limit_resume_dat")) # Use translated text (simplified resume)
                        if collection_popup_active and self.collection_progress_label_var is not None:
                             self.collection_progress_label_var.set(self.translate("api_rate_limit_resume_collection")) # Use translated text (simplified resume)

                        self.master.update_idletasks()
                    retries += 1
                    self.status_bar_text_var.set(self.translate("api_rate_limit_resume_status", os.path.basename(current_url_for_msg))) # Use translated text
                    # Restore original progress messages if popups are active
                    if fetch_popup_active and self.fetch_progress_label_var is not None:
                         # Restore a more specific message if needed, or a generic one
                         self.fetch_progress_label_var.set(self.translate("api_rate_limit_resume_fetch")) # Generic resume message (already set above)
                    if dat_popup_active and self.dat_progress_label_var is not None:
                        self.dat_progress_label_var.set(self.translate("api_rate_limit_resume_dat")) # Generic resume message (already set above)
                    if collection_popup_active and self.collection_progress_label_var is not None:
                         self.collection_progress_label_var.set(self.translate("api_rate_limit_resume_collection")) # Generic resume message (already set above)

                    self.master.update_idletasks()
                    continue
                else:
                    self.status_bar_text_var.set(self.translate("api_http_error", e.response.status_code, os.path.basename(current_url_for_msg))) # Add this key
                    messagebox.showerror(self.translate("api_error_message_title"), self.translate("api_error_message_text", e.response.status_code, e.request.url, e.response.text[:200])) # Add this key
                    return None
            except requests.exceptions.Timeout:
                self.status_bar_text_var.set(self.translate("api_timeout", os.path.basename(current_url_for_msg))) # Use translated text
                if retries < max_retries_on_429:
                    retries += 1
                    wait_time = initial_backoff_s * (2 ** retries)
                    wait_time = min(wait_time, 60)
                    self.status_bar_text_var.set(self.translate("api_timeout_retry_wait", wait_time, retries + 1, max_retries_on_429+1)) # Use translated text
                    time.sleep(wait_time)
                    continue
                else:
                    messagebox.showerror(self.translate("api_timeout_message_title"), self.translate("api_timeout_message_text", max_retries_on_429+1, url)) # Use translated text
                    return None
            except requests.exceptions.RequestException as e:
                self.status_bar_text_var.set(self.translate("api_connection_error", os.path.basename(current_url_for_msg))) # Use translated text
                messagebox.showerror(self.translate("api_connection_error_message_title"), self.translate("api_connection_error_message_text", e, url)) # Use translated text
                return None
        self.status_bar_text_var.set(self.translate("api_max_retries_reached", max_retries_on_429+1, os.path.basename(current_url_for_msg))) # Use translated text
        messagebox.showwarning(self.translate("api_limit_reached_warning_title"), self.translate("api_limit_reached_warning_text", os.path.basename(current_url_for_msg), max_retries_on_429+1)) # Use translated text
        return None


    def test_login(self):
        """Tests login credentials by fetching user profile"""
        self.status_bar_text_var.set(self.translate("login_testing")) # Use translated text
        self.master.update_idletasks()
        # Check Tkinter variables directly, they are now initialized
        if not self.username.get() or not self.api_key.get():
            messagebox.showerror(self.translate("login_error_message_title"), self.translate("auth_error_message_text")) # Use translated text
            self.login_status_light.config(bg="red")
            self.login_status_label.config(text=self.translate("status_error")) # Use translated text (Generic error for login issues)
            self.status_bar_text_var.set(self.translate("login_failed_input_missing")) # Use translated text
            self.on_selection_change(None) # Update button states
            return

        params_for_request = {'u': self.username.get()}
        data = self._make_api_request(API_USER_PROFILE_URL, params=params_for_request, authenticate=True)

        if data and isinstance(data, dict) and "User" in data and data["User"].lower() == self.username.get().lower():
            self.login_status_light.config(bg="green")
            self.login_status_label.config(text=self.translate("status_connected")) # Use translated text
            self.status_bar_text_var.set(self.translate("login_success_loading_consoles")) # Use translated text
            self.save_config() # Save credentials on successful login
            self.load_consoles() # This will call on_selection_change at the end
        else:
            self.login_status_light.config(bg="red")
            self.login_status_label.config(text=self.translate("status_failed")) # Use translated text
            error_reason = self.translate("login_failed_unknown") # Add this key
            if data is None:
                 error_reason = self.translate("login_failed_api_none") # Use translated text
            elif isinstance(data, dict):
                if "User" not in data:
                     error_reason = self.translate("login_failed_api_no_user", str(data)[:100]) # Use translated text
                elif data["User"].lower() != self.username.get().lower():
                     error_reason = self.translate("login_failed_api_user_mismatch", data.get('User')) # Use translated text
            else:
                error_reason = self.translate("login_failed_api_unexpected_format", str(data)[:100]) # Use translated text
            self.status_bar_text_var.set(self.translate("login_failed_reason", error_reason)) # Add this key: "Login failed. %s"
            messagebox.showerror(self.translate("login_error_message_title"), self.translate("login_error_message_text", error_reason)) # Use translated text
            self.console_dropdown.config(state="disabled")
            self.console_dropdown.set('')
            self.console_id_to_name_map = {} # Clear mappings
            self.console_name_to_id_map = {}
            self.on_selection_change(None) # Update button states

    def load_consoles(self):
        """Loads the list of console IDs and names from the API"""
        self.status_bar_text_var.set(self.translate("consoles_loading")) # Use translated text
        self.master.update_idletasks()
        consoles_raw = self._make_api_request(API_CONSOLE_IDS_URL, authenticate=True)

        if consoles_raw and isinstance(consoles_raw, list):
            valid_consoles = [item for item in consoles_raw if isinstance(item, dict) and 'Name' in item and 'ID' in item]
            if not valid_consoles and consoles_raw:
                messagebox.showwarning(self.translate("consoles_warning_format_title"), self.translate("consoles_warning_format_text")) # Use translated text
                self.status_bar_text_var.set(self.translate("status_consoles_unexpected_format")) # Use translated text
                self.on_selection_change(None)
                return
            # Populate console mapping dictionaries
            # Ensure IDs are stored as strings in map keys
            self.console_id_to_name_map = {str(item['ID']): str(item['Name']) for item in valid_consoles}
            self.console_name_to_id_map = {str(item['Name']): str(item['ID']) for item in valid_consoles}

            sorted_console_names = sorted(self.console_name_to_id_map.keys())
            if not sorted_console_names:
                messagebox.showinfo(self.translate("info_title"), self.translate("consoles_info_no_valid")) # Use translated text
                self.status_bar_text_var.set(self.translate("status_no_consoles_found")) # Use translated text
                self.console_dropdown.config(state="disabled")
                # Ensure all buttons are disabled if no consoles
                self.fetch_data_button.config(state="disabled")
                self.create_dat_button.config(state="disabled")
                # Use the new button variables
                self.create_retropie_collection_button.config(state="disabled")
                self.create_batocera_collection_button.config(state="disabled")

                self.on_selection_change(None)
                return
            self.console_dropdown['values'] = sorted_console_names
            self.console_dropdown.config(state="readonly")
            self.status_bar_text_var.set(self.translate("status_consoles_loaded")) # Use translated text
            # Keep the currently selected console if it exists in the new list, otherwise select the first
            current_selection = self.selected_console_id_var.get()
            if current_selection in sorted_console_names:
                 self.console_dropdown.set(current_selection)
            elif sorted_console_names:
                self.console_dropdown.set(sorted_console_names[0])
            else:
                 self.console_dropdown.set('') # Should not happen if sorted_console_names is not empty

            self.on_selection_change(None) # Call to update buttons based on (new) default selection
        elif consoles_raw is None: # _make_api_request failed
            self.status_bar_text_var.set(self.translate("status_consoles_loading_error")) # Use translated text
            # Keep controls disabled
            self.console_dropdown.config(state="disabled")
            self.fetch_data_button.config(state="disabled")
            self.create_dat_button.config(state="disabled")
            # Use the new button variables
            self.create_retropie_collection_button.config(state="disabled")
            self.create_batocera_collection_button.config(state="disabled")

            self.on_selection_change(None)
        else: # API returned something, but not a list
            self.status_bar_text_var.set(self.translate("status_no_consoles_found_api_format")) # Use translated text
            messagebox.showinfo(self.translate("info_title"), self.translate("consoles_info_no_consoles_found_api_text", str(consoles_raw)[:200])) # Use translated text
            self.console_dropdown.config(state="disabled")
            self.fetch_data_button.config(state="disabled")
            self.create_dat_button.config(state="disabled")
            # Use the new button variables
            self.create_retropie_collection_button.config(state="disabled")
            self.create_batocera_collection_button.config(state="disabled")

            self.on_selection_change(None)


    def on_selection_change(self, event):
        """Enable/disable buttons based on selections, login status, paths, and data availability."""
        # print("\n--- on_selection_change triggered ---")
        console_name = self.selected_console_id_var.get()
        # Use the name-to-id map now
        console_id = self.console_name_to_id_map.get(console_name)
        # print(f"DEBUG: console_name='{console_name}', console_id='{console_id}'")

        # Update login status label text based on state indicator (the color)
        # This is updated in update_ui_language and clear_credentials, relying on the color here is okay for logic
        is_connected = (self.login_status_light.cget("bg") == "green")

        # print(f"DEBUG: is_connected={is_connected}")

        dat_path_selected = bool(self.dat_save_path.get() and os.path.isdir(self.dat_save_path.get()))
        # print(f"DEBUG: dat_path_selected={dat_path_selected} (Path: '{self.dat_save_path.get()}')")

        collection_cfg_path_selected = bool(self.collection_cfg_save_path.get() and os.path.isdir(self.collection_cfg_save_path.get()))
        # print(f"DEBUG: collection_cfg_path_selected={collection_cfg_path_selected} (Path: '{self.collection_cfg_save_path.get()}')")

        # RetroPie Pfad muss nur gesetzt sein, nicht lokal existieren
        retropie_rom_path_selected = bool(self.retropie_base_path.get())
        # print(f"DEBUG: retropie_rom_path_selected={retropie_rom_path_selected} (Path: '{self.retropie_base_path.get()}')")

        # Batocera Pfad muss nur gesetzt sein, nicht lokal existieren
        batocera_rom_path_selected = bool(self.batocera_base_path.get())
        # print(f"DEBUG: batocera_rom_path_selected={batocera_rom_path_selected} (Path: '{self.batocera_base_path.get()}')")


        data_available = False
        # Caching is always on, so we check if data is in memory OR cache file exists
        # Ensure console_id is string when checking cached_data key
        console_id_str = str(console_id) if console_id else None

        if console_id_str:
            if console_id_str in self.cached_data and isinstance(self.cached_data[console_id_str], list) and self.cached_data[console_id_str]:
                 data_available = True
                 # print(f"DEBUG: Data for console_id '{console_id_str}' found in memory cache ({len(self.cached_data[console_id_str])} items).")
            else:
                 cache_file = self.get_cache_filename(console_id_str)
                 # Check if file exists and is not empty json "[]"
                 if os.path.exists(cache_file) and os.path.getsize(cache_file) > 2:
                     data_available = True
                     # print(f"DEBUG: Data for console_id '{console_id_str}' potentially available in cache file: {cache_file}")
                 # else:
                    # print(f"DEBUG: Cache file for console_id '{console_id_str}' not found, empty, or invalid.")
        # else:
            # print("DEBUG: No console_id selected, so data_available is False.")
        # print(f"DEBUG: data_available={data_available}")

        # Enable "Fetch Data"
        # Fetch data is possible if a console is selected AND user is connected
        # Also check if a fetch is *not* already in progress
        if console_id_str and is_connected and self._fetch_worker_thread is None:
            self.fetch_data_button.config(state="normal")
            # print("DEBUG: fetch_data_button state: normal")
        else:
            self.fetch_data_button.config(state="disabled")
            # print("DEBUG: fetch_data_button state: disabled")

        # Enable "Create DAT"
        if console_id_str and is_connected and data_available and dat_path_selected:
            self.create_dat_button.config(state="normal")
            # print("DEBUG: create_dat_button state: normal")
        else:
            self.create_dat_button.config(state="disabled")
            # print("DEBUG: create_dat_button state: disabled")

        # Enable "Create RetroPie Collection" (Use the new button variable)
        # Condition: console_id_str, is_connected, data_available, collection_cfg_path_selected, retropie_rom_path_selected
        # No change needed here, logic remains the same but now uses rom_extension_var internally
        if console_id_str and \
           is_connected and \
           data_available and \
           collection_cfg_path_selected and \
           retropie_rom_path_selected:
            # Use the new button variable
            self.create_retropie_collection_button.config(state="normal")
            # print("DEBUG: create_retropie_collection_button state: normal")
        else:
            # Use the new button variable
            self.create_retropie_collection_button.config(state="disabled")
            # print("DEBUG: create_retropie_collection_button state: disabled")

        # Enable "Create Batocera Collection" (New button)
        # Condition: console_id_str, is_connected, data_available, collection_cfg_path_selected, batocera_rom_path_selected
        # No change needed here, logic remains the same but now uses rom_extension_var internally
        if console_id_str and \
           is_connected and \
           data_available and \
           collection_cfg_path_selected and \
           batocera_rom_path_selected:
            self.create_batocera_collection_button.config(state="normal")
            # print("DEBUG: create_batocera_collection_button state: normal")
        else:
            self.create_batocera_collection_button.config(state="disabled")
            # print("DEBUG: create_batocera_collection_button state: disabled")

        # print("--- end of on_selection_change ---")


    def _get_typical_extension(self, console_name):
        # This method is now less critical as the user provides the extension,
        # but it's kept as a fallback for generating a default filename base
        # if no filename is available in the API data.
        ext_map = {
            "nes": "nes", "nintendo entertainment system": "nes", "famicom": "nes",
            "snes": "sfc", "super nintendo": "sfc", "super famicom": "sfc",
            "mega drive": "md", "sega genesis": "md", "genesis": "md", "megadrive": "md",
            "game boy": "gb", "gameboy": "gb",
            "game boy color": "gbc", "gameboy color": "gbc",
            "game boy advance": "gba", "gameboy advance": "gba",
            "playstation": "cue", "psx": "cue", "ps1": "cue", "sony playstation": "cue",
            "nintendo 64": "n64", "n64": "z64", # N64 can be v64, z64, n64 - z64 is common
            "pc engine": "pce", "turbografx-16": "pce", "turbografx": "pce", "tg-16": "pce",
            "master system": "sms", "sega master system": "sms", "sms": "sms",
            "msx": "rom", "msx2": "rom",
            "neo geo pocket": "ngp",
            "neo geo pocket color": "ngc", "ngpc": "ngc",
            "arcade": "zip", "mame": "zip", # Arcade ROMs are typically zipped
            "atari 2600": "a26", "vcs": "a26", "atari vcs": "a26",
            "atari lynx": "lnx", "lynx": "lnx",
            "wonderswan": "ws",
            "wonderswan color": "wsc",
            "virtual boy": "vb", "virtualboy": "vb",
            "sega 32x": "32x", "32x": "32x",
            "sega cd": "cue", "mega-cd": "cue", "segacd": "cue",
            "atari jaguar": "j64", "jaguar": "j64", # Jaguar can be bin, j64
            "atari jaguar cd": "cue",
            "dreamcast": "gdi", "sega dreamcast": "gdi", # Dreamcast can be cdi, gdi, iso
            "psp": "iso", "playstation portable": "iso", # PSP can be iso, cso
            "nds": "nds", "nintendo ds": "nds",
            "gamecube": "iso", "nintendo gamecube": "iso", "ngc": "iso", "dol": "dol", # GameCube can be iso, gcm, dol
            "wii": "iso", "nintendo wii": "iso", "wbfs": "wbfs", # Wii can be iso, wbfs
            "xbox": "iso", "microsoft xbox": "iso", # Xbox can be iso, xbe
            "playstation 2": "iso", "ps2": "iso", "sony playstation 2": "iso", # PS2 can be iso, bin
            "3do": "iso", "3do interactive multipayer": "iso",
            "colecovision": "col",
            "intellivision": "int",
            "vectrex": "vec",
            "amstrad cpc": "dsk",
            "commodore 64": "d64", "c64": "d64",
            "zx spectrum": "tzx", "spectrum": "tzx", # ZX Spectrum can be zx, tap, tzx, dsk, trd, scl, szx, etc.
        }
        console_name_lower = console_name.lower()
        if console_name_lower in ext_map:
            return ext_map[console_name_lower]
        for key, value in ext_map.items():
            if key in console_name_lower:
                return value
        sanitized = "".join(c for c in console_name if c.isalnum()).lower()
        return sanitized if sanitized else "unknownsystem"


    def _get_system_short_name(self, console_name):
        short_names = {
            "nes": "nes", "nintendo entertainment system": "nes", "famicom": "nes",
            "snes": "snes", "super nintendo": "snes", "super famicom": "snes",
            "mega drive": "megadrive", "sega genesis": "genesis", "genesis": "genesis", "megadrive": "megadrive",
            "game boy": "gb", "gameboy": "gb",
            "game boy color": "gbc", "gameboy color": "gbc",
            "game boy advance": "gba", "gameboy advance": "gba",
            "playstation": "psx", "psx": "psx", "ps1": "psx", "sony playstation": "psx",
            "nintendo 64": "n64", "n64": "n64",
            "pc engine": "pcengine", "turbografx-16": "pcengine", "turbografx": "pcengine", "tg-16": "pcengine",
            "master system": "mastersystem", "sega master system": "mastersystem", "sms": "mastersystem",
            "msx": "msx", "msx2": "msx",
            "neo geo pocket": "ngp",
            "neo geo pocket color": "ngpc", "ngpc": "ngpc",
            "arcade": "arcade", "mame": "arcade",
            "atari 2600": "atari2600", "vcs": "atari2600", "atari vcs": "atari2600",
            "atari lynx": "lynx", "lynx": "lynx",
            "wonderswan": "wonderswan",
            "wonderswan color": "wonderswancolor",
            "virtual boy": "virtualboy", "virtualboy": "virtualboy",
            "sega 32x": "sega32x", "32x": "sega32x",
            "sega cd": "segacd", "mega-cd": "segacd", "segacd": "segacd",
            "atari jaguar": "jaguar", "jaguar": "jaguar",
            "atari jaguar cd": "jaguarcd",
            "dreamcast": "dreamcast", "sega dreamcast": "dreamcast",
            "psp": "psp", "playstation portable": "psp",
            "nds": "nds", "nintendo ds": "nds",
            "gamecube": "gc", "nintendo gamecube": "gc", "ngc": "gc",
            "wii": "wii", "nintendo wii": "wii",
            "xbox": "xbox", "microsoft xbox": "xbox",
            "playstation 2": "ps2", "ps2": "ps2", "sony playstation 2": "ps2",
            "3do": "3do", "3do interactive multipayer": "3do",
            "colecovision": "coleco",
            "intellivision": "intellivision",
            "vectrex": "vectrex",
            "amstrad cpc": "amstradcpc",
            "commodore 64": "c64", "c64": "c64",
            "zx spectrum": "zxspectrum", "spectrum": "zxspectrum",
        }
        console_name_lower = console_name.lower()
        if console_name_lower in short_names:
            return short_names[console_name_lower]
        for key, value in short_names.items():
            if key in console_name_lower:
                return value
        sanitized = "".join(c for c in console_name if c.isalnum()).lower()
        return sanitized if sanitized else "unknownsystem"

    def fetch_data(self):
        """Prepare for fetching data and start the worker thread."""
        console_name = self.selected_console_id_var.get()
        # Use the name-to-id map now
        console_id = self.console_name_to_id_map.get(console_name)

        if not console_id:
            messagebox.showerror(self.translate("data_fetch_invalid_console_error_title"), self.translate("data_fetch_invalid_console_error_text")) # Use translated text
            return

        # Ensure console_id is string for cache/data operations
        console_id_str = str(console_id)

        # Prevent starting a new fetch if one is already in progress
        if self._fetch_worker_thread is not None and self._fetch_worker_thread.is_alive():
             print("DEBUG: Fetch already in progress, ignoring request.")
             return # Do nothing if fetching is already happening


        print(f"\nDEBUG: Starting data fetch process for console: {console_name} (ID: {console_id_str})")
        self.status_bar_text_var.set(self.translate("status_data_fetch_start", console_name)) # Use translated text
        # Disable fetch button immediately
        self.fetch_data_button.config(state="disabled")
        # Disable all creation buttons during fetch
        self.create_dat_button.config(state="disabled")
        self.create_retropie_collection_button.config(state="disabled") # Use the new variable
        self.create_batocera_collection_button.config(state="disabled") # Use the new variable

        self.master.update_idletasks()

        # Caching is always on now, so we check cache first (still in main thread)
        print(self.translate("data_fetch_checking_cache")) # Use translated text
        cached_console_data = self.load_from_cache(console_id_str) # Use string ID for cache
        if cached_console_data is not None and isinstance(cached_console_data, list):
            print(f"DEBUG: Using cached data for {console_name}. {len(cached_console_data)} games loaded.") # This can remain debug or translate
            self.cached_data[console_id_str] = cached_console_data # Store in memory using string ID
            # Update status bar - check if the original fetch message is still there, otherwise set a generic cached message
            current_status = self.status_bar_text_var.get()
            if self.translate("status_data_fetch_start", console_name) in current_status:
                 self.status_bar_text_var.set(self.translate("data_fetch_cache_loaded_status", console_name)) # Add this key
            self.on_selection_change(None) # Update button states now that data is available
            return # Exit if data loaded from cache


        # If no cache, proceed to fetch from API in a separate thread
        print(self.translate("data_fetch_game_list")) # Use translated text
        # Fetch the initial game list first in the main thread to get total count for progress bar
        game_list_params = {'i': console_id_str} # Use string ID for API params
        initial_game_list_data = self._make_api_request(API_GAME_LIST_URL, params=game_list_params, authenticate=True)

        if not initial_game_list_data or not isinstance(initial_game_list_data, list):
            message = self.translate("api_error_fetch_games_none", console_name) if initial_game_list_data is None else self.translate("api_error_fetch_games_not_list", console_name) # Use translated text
            details = ""
            if not isinstance(initial_game_list_data, list):
                details = self.translate("api_error_message_fetch_games_text", "", f"\nAntwort (Beginn): {str(initial_game_list_data)[:200]}").split('\n', 1)[1] # Extract only the response part
            messagebox.showerror(self.translate("api_error_message_fetch_games_title"), self.translate("api_error_message_fetch_games_text", message, details)) # Use translated text
            self.status_bar_text_var.set(message)
            # Re-enable fetch button as no fetch worker was started
            self.fetch_data_button.config(state="normal")
            self.on_selection_change(None) # Update other button states
            return

        if not initial_game_list_data:
            messagebox.showinfo(self.translate("info_title"), self.translate("data_fetch_info_no_games_api", console_name)) # Use translated text
            self.status_bar_text_var.set(self.translate("status_no_games_found_api", console_name)) # Use translated text
            # Even if no games, save empty list to cache to indicate we checked
            self.cached_data[console_id_str] = [] # Store empty list using string ID
            self.save_to_cache(console_id_str, []) # Save empty list to cache
            # Re-enable fetch button as no fetch worker was started
            self.fetch_data_button.config(state="normal")
            self.on_selection_change(None) # Update button states
            return

        total_games = len(initial_game_list_data)

        # Use fetch-specific progress popup variable
        self._fetch_progress_popup = tk.Toplevel(self.master) # Store reference
        popup = self._fetch_progress_popup # Use local name for convenience
        popup.title(self.translate("data_fetch_progress_title")) # Use translated text
        # Set initial size, position calculated later
        fetch_popup_width = 450
        fetch_popup_height = 150
        popup.geometry(f"{fetch_popup_width}x{fetch_popup_height}")
        popup.resizable(False, False)

        # Use fetch-specific label variable
        self.fetch_progress_label_var.set(self.translate("status_data_fetch_start", console_name)) # Use translated text
        game_progress_label_var = tk.StringVar() # This can remain local for game-specific details in the popup
        ttk.Label(popup, textvariable=self.fetch_progress_label_var, wraplength=430).pack(pady=(10,0), padx=10, fill="x")
        ttk.Label(popup, textvariable=game_progress_label_var, wraplength=430).pack(pady=(0,5), padx=10, fill="x")

        progress_bar = ttk.Progressbar(popup, orient="horizontal", length=430, mode="determinate")
        progress_bar.pack(pady=10, padx=10)
        progress_bar["maximum"] = total_games


        # --- Calculate and set fetch popup position ---
        self.master.update_idletasks() # Ensure main window geometry is up-to-date
        main_width = self.master.winfo_width()
        main_height = self.master.winfo_height()
        main_x = self.master.winfo_x()
        main_y = self.master.winfo_y()

        center_x = main_x + (main_width // 2) - (fetch_popup_width // 2)
        center_y = main_y + (main_height // 2) - (fetch_popup_height // 2)

        screen_width = self.master.winfo_screenwidth()
        screen_height = self.master.winfo_screenheight()
        center_x = max(0, min(center_x, screen_width - fetch_popup_width))
        center_y = max(0, min(center_y, screen_height - fetch_popup_height))

        popup.geometry(f'{fetch_popup_width}x{fetch_popup_height}+{center_x}+{center_y}')
        # --- End position calculation ---

        # Make it modal AFTER positioning
        popup.grab_set()
        # Ensure popup is removed if closed via window manager
        # Override the close button to potentially cancel the operation later if needed
        # For now, just destroying is fine, but a robust solution might ask the user if they want to cancel
        popup.protocol("WM_DELETE_WINDOW", self._cancel_fetch) # Use a specific cancel method

        # Start the data fetching in a separate thread
        self._fetch_worker_thread = threading.Thread(target=self._fetch_worker,
                                                     args=(console_id_str, console_name, initial_game_list_data,
                                                           progress_bar, game_progress_label_var, popup))
        self._fetch_worker_thread.daemon = True # Allow the application to exit even if the thread is running
        self._fetch_worker_thread.start()

    def _cancel_fetch(self):
        """Handle cancellation attempt during fetch."""
        # For a simple implementation, just destroy the popup.
        # A more advanced implementation might use a flag the worker thread checks.
        if hasattr(self, '_fetch_progress_popup') and self._fetch_progress_popup and tk.Toplevel.winfo_exists(self._fetch_progress_popup):
             # Ask for confirmation before cancelling the thread operation
             confirm_cancel = messagebox.askyesno(
                 self.translate("cancel_fetch_title"), # Add this translation key
                 self.translate("cancel_fetch_text"), # Add this translation key
                 parent=self._fetch_progress_popup
             )
             if confirm_cancel:
                 # In a more complex app, you'd signal the worker thread to stop.
                 # Here, we just destroy the popup. The worker thread will continue
                 # but its GUI updates will just fail silently or error out if
                 # they try to access the destroyed widgets.
                 # A proper cancellation requires the worker thread to periodically
                 # check a flag. Let's add a cancellation flag.
                 self._cancel_fetch_flag = True # Set a flag for the worker to check
                 self._fetch_progress_popup.destroy()
                 self._fetch_progress_popup = None # Clear reference
                 self.status_bar_text_var.set(self.translate("status_fetch_cancelled")) # Add this translation key
                 # Buttons will be re-enabled in _on_fetch_complete when the worker finishes
                 # (even if it finishes due to cancellation) or explicitly here if the worker
                 # is designed to exit quickly on cancellation.
                 # For now, let the worker finish naturally or adapt worker to exit on flag.
                 # Let's adapt the worker to check the flag.
             # If not confirmed, do nothing, popup stays open
        else:
             # If popup is not open, just print a debug message
             print("DEBUG: Attempted to cancel fetch, but popup is not active.")


    def _fetch_worker(self, console_id_str, console_name, initial_game_list_data, progress_bar, game_progress_label_var, popup):
        """Worker function to fetch data in a separate thread."""
        print(f"DEBUG: Fetch worker thread started for console ID: {console_id_str}")
        processed_data_for_cache = []
        total_games = len(initial_game_list_data)
        api_call_delay = 0.6 # Adjusted from 0.6 to allow slightly faster processing, monitor API for 429s

        # Initialize cancellation flag
        self._cancel_fetch_flag = False

        try:
            for index, game_entry in enumerate(initial_game_list_data):
                # Check cancellation flag periodically
                if self._cancel_fetch_flag:
                     print("DEBUG: Fetch worker detected cancellation flag, stopping.")
                     break # Exit the loop if cancellation is requested

                if not isinstance(game_entry, dict):
                    # Schedule print statement
                    self.master.after(0, print, self.translate("data_fetch_skipping_invalid_entry", index, game_entry)) # Use translated text
                    # Schedule progress bar update - CORRECTED LINE
                    self.master.after(0, lambda: progress_bar.config(value=index + 1))
                    # Schedule popup and master update (redundant but ensures updates)
                    self.master.after(0, popup.update_idletasks)
                    self.master.after(0, self.master.update_idletasks)
                    continue

                game_id = game_entry.get('ID')
                game_title = game_entry.get('Title', f'Unbekanntes Spiel ID {game_id}') # Keep fallback as is or translate

                # Schedule progress label updates
                self.master.after(0, self.fetch_progress_label_var.set, self.translate("data_fetch_processing_game", index+1, total_games, console_name)) # Use translated text
                self.master.after(0, game_progress_label_var.set, f"{game_title[:50]}...") # This is dynamic, keep as is
                # Schedule progress bar update - CORRECTED LINE
                self.master.after(0, lambda: progress_bar.config(value=index + 1))
                 # Schedule popup and master update (redundant but ensures updates)
                self.master.after(0, popup.update_idletasks)
                self.master.after(0, self.master.update_idletasks)


                if not game_id:
                    # Schedule print statement
                    self.master.after(0, print, self.translate("data_fetch_skipping_missing_id", game_entry)) # Use translated text
                    continue
                # Use string ID for API calls
                game_id_str = str(game_id)

                if index > 0: # Apply delay after the first game
                    time.sleep(api_call_delay)

                # API calls from worker thread - _make_api_request needs to handle scheduled status updates internally
                game_hashes_params = {'i': game_id_str, 'g': console_id_str} # Use string IDs
                game_hashes_data = self._make_api_request(API_GET_GAME_HASHES_URL, params=game_hashes_params, authenticate=True)

                extended_info = {}
                if (self.include_achievements_var.get() or self.include_patch_urls_var.get()) and not self._cancel_fetch_flag: # Check flag before next API call
                    extended_params = {'i': game_id_str, 'g': console_id_str} # Use string IDs
                    extended_data_api_response = self._make_api_request(API_GET_GAME_EXTENDED_URL, params=extended_params, authenticate=True)

                    if extended_data_api_response and isinstance(extended_data_api_response, dict):
                        if self.include_achievements_var.get():
                            extended_info['num_achievements'] = extended_data_api_response.get('NumAchievements', 0)
                            extended_info['points'] = extended_data_api_response.get('Points', 0)
                        if self.include_patch_urls_var.get():
                            patch_data = extended_data_api_response.get('PatchData')
                            if isinstance(patch_data, dict):
                                extended_info['patch_url'] = patch_data.get('URL', '')
                                extended_info['patch_md5'] = patch_data.get('Hash', '')


                md5_list = []
                if game_hashes_data and isinstance(game_hashes_data, dict) and 'Results' in game_hashes_data:
                    results_list = game_hashes_data['Results']
                    if isinstance(results_list, list):
                        for item in results_list:
                            if isinstance(item, dict):
                                hash_md5 = item.get('MD5')
                                hash_name = item.get('Name', 'Unknown Filename') # Keep fallback or translate
                                hash_labels = item.get('Labels', [])
                                hash_status = item.get('Status')
                                if hash_md5 and isinstance(hash_md5, str) and len(hash_md5) == 32 and all(c in '0123456789abcdefABCDEF' for c in hash_md5):
                                    md5_list.append({
                                        'md5': hash_md5.lower(),
                                        'name': hash_name,
                                        'labels': hash_labels if isinstance(hash_labels, list) else [],
                                        'status': hash_status
                                    })

                if not md5_list:
                    # Schedule print statement
                    self.master.after(0, print, self.translate("data_fetch_skipping_no_hashes", game_id, game_title)) # Use translated text
                    continue

                # Only append if not cancelled before processing this game's data
                if not self._cancel_fetch_flag:
                    processed_data_for_cache.append({
                        'id': game_id_str, # Store ID as string in cached data
                        'title': game_title,
                        'hashes': md5_list,
                        'extended_info': extended_info if extended_info else None
                    })

        except Exception as e:
            # Handle unexpected errors in the worker thread
            import traceback
            error_msg = f"Unexpected error during data fetch: {e}\n{traceback.format_exc()}"
            print(f"ERROR: {error_msg}")
            # Schedule error message display and status update on main thread
            self.master.after(0, messagebox.showerror, self.translate("data_fetch_unexpected_error_title"), self.translate("data_fetch_unexpected_error_text", str(e))) # Add this key
            self.master.after(0, self.status_bar_text_var.set, self.translate("status_data_fetch_unexpected_error")) # Add this key
            processed_data_for_cache = None # Indicate failure


        finally:
            # This block runs whether the loop finished or broke (due to cancellation)
            # Schedule the completion handler to run on the main thread
            print("DEBUG: Fetch worker finished or cancelled, scheduling completion handler.")
            self.master.after(0, self._on_fetch_complete, console_id_str, processed_data_for_cache)


    def _on_fetch_complete(self, console_id_str, fetched_data):
        """Handle data fetch completion on the main Tkinter thread."""
        print(f"DEBUG: _on_fetch_complete called for console ID: {console_id_str}")

        # Destroy fetch progress popup
        if hasattr(self, '_fetch_progress_popup') and self._fetch_progress_popup and tk.Toplevel.winfo_exists(self._fetch_progress_popup):
             self._fetch_progress_popup.destroy()
             self._fetch_progress_popup = None # Clear reference

        # Clear the worker thread reference
        self._fetch_worker_thread = None
        self._cancel_fetch_flag = False # Reset cancellation flag

        # Process results
        if fetched_data is not None: # fetched_data is None if an unexpected error occurred in the worker
            if fetched_data: # Check if the list is not empty
                 self.cached_data[console_id_str] = fetched_data # Store in-memory using string ID
                 cache_saved = self.save_to_cache(console_id_str, fetched_data) # Use string ID
                 if cache_saved:
                     self.status_bar_text_var.set(self.translate("data_fetch_cache_save_success", self.console_id_to_name_map.get(console_id_str, console_id_str))) # Use translated text
                 else:
                     # save_to_cache updates status bar internally (scheduled)
                     pass # Status update already set by save_to_cache

                 messagebox.showinfo(self.translate("data_fetch_completed_title"),
                                    self.translate("data_fetch_completed_text",
                                                   self.console_id_to_name_map.get(console_id_str, console_id_str),
                                                   len(fetched_data))) # Use translated text
                 print(self.translate("data_fetch_completed", self.console_id_to_name_map.get(console_id_str, console_id_str))) # Use translated text

            else: # fetched_data is an empty list
                 self.cached_data[console_id_str] = [] # Store empty list
                 self.save_to_cache(console_id_str, []) # Save empty list to cache
                 self.status_bar_text_var.set(self.translate("status_no_games_with_hashes", self.console_id_to_name_map.get(console_id_str, console_id_str))) # Use translated text
                 messagebox.showinfo(self.translate("info_title"), self.translate("data_fetch_no_games_with_hashes_info", self.console_id_to_name_map.get(console_id_str, console_id_str))) # Add this key

        else: # fetched_data is None (unexpected error in worker)
            # Error message and status are already set by the worker before calling this completion handler
            # Ensure buttons are re-enabled even after an error
            pass # No extra action needed here, error was reported by worker

        # Re-enable buttons
        self.on_selection_change(None)


    def create_dat_file(self):
        """Create DAT file from cached or fresh data using clrmamepro format."""
        console_name = self.selected_console_id_var.get()
        # Use the name-to-id map now
        console_id = self.console_name_to_id_map.get(console_name)
        dat_file_dir = self.dat_save_path.get()

        if not console_id:
            messagebox.showerror(self.translate("dat_creation_invalid_console_error_title"), self.translate("dat_creation_invalid_console_error_text")) # Use translated text
            return
        if not dat_file_dir or not os.path.isdir(dat_file_dir):
            messagebox.showerror(self.translate("dat_creation_invalid_save_path_error_title"), self.translate("dat_creation_invalid_save_path_error_text")) # Use translated text
            return

        # Ensure console_id is string for data access
        console_id_str = str(console_id)

        current_console_data = self.cached_data.get(console_id_str) # Use string ID
        # Caching is always on, try loading from cache if not in memory
        if current_console_data is None or not current_console_data:
            # Load from cache (load_from_cache handles status updates)
            current_console_data = self.load_from_cache(console_id_str) # Use string ID
            if current_console_data is not None and isinstance(current_console_data, list):
                 self.cached_data[console_id_str] = current_console_data # Load into memory using string ID
            else:
                messagebox.showwarning(self.translate("dat_creation_no_data_warning_title"), self.translate("dat_creation_no_data_warning_text", console_name)) # Use translated text
                self.on_selection_change(None)
                return


        if not current_console_data: # Check again after attempting to load from cache
            messagebox.showinfo(self.translate("dat_creation_no_data_info_title"), self.translate("dat_creation_no_data_info_text", console_name)) # Use translated text
            self.status_bar_text_var.set(self.translate("status_no_data_for_dat", console_name)) # Use translated text
            self.on_selection_change(None)
            return

        # print(f"DEBUG: Creating DAT file for {console_name} with {len(current_console_data)} game entries...")
        self.status_bar_text_var.set(self.translate("status_dat_creation_start", console_name)) # Use translated text
        # Disable all creation buttons during DAT creation
        self.create_dat_button.config(state="disabled")
        self.fetch_data_button.config(state="disabled")
        self.create_retropie_collection_button.config(state="disabled") # Use the new variable
        self.create_batocera_collection_button.config(state="disabled") # Use the new variable
        self.master.update_idletasks()

        total_games_in_dat = len(current_console_data)
        games_with_hashes_count = 0
        games_with_achievements_count = 0
        dat_header = [
            "clrmamepro (",
            f"\tname \"{console_name} - RetroAchievements\"",
            f"\tdescription \"{console_name} - RetroAchievements (RA Hashes - {datetime.now().strftime('%Y-%m-%d')})\"",
            f"\tversion \"{datetime.now().strftime('%Y%m%d-%H%M%S')}\"",
            f"\tcomment \"{self.translate('dat_comment')}\"", # Add this key
            f"\tauthor \"{self.translate('dat_author')}\"", # Add this key
            ")", ""
        ]
        game_entries_for_dat_file = []

        # Use dat-specific progress popup variable
        self._dat_progress_popup = tk.Toplevel(self.master) # Store reference
        popup = self._dat_progress_popup # Use local name for convenience
        popup.title(self.translate("dat_creation_progress_title")) # Use translated text
        # Set initial size, position calculated later
        dat_popup_width = 450
        dat_popup_height = 130
        popup.geometry(f"{dat_popup_width}x{dat_popup_height}")
        popup.resizable(False, False)


        # Use dat-specific label variable
        self.dat_progress_label_var.set(self.translate("status_dat_creation_start", console_name)) # Use translated text
        ttk.Label(popup, textvariable=self.dat_progress_label_var, wraplength=430).pack(pady=10, padx=10, fill="x")
        dat_progress_bar = ttk.Progressbar(popup, orient="horizontal", length=430, mode="determinate")
        dat_progress_bar.pack(pady=10, padx=10)
        dat_progress_bar["maximum"] = total_games_in_dat

        # --- Calculate and set dat popup position ---
        self.master.update_idletasks() # Ensure main window geometry is up-to-date
        main_width = self.master.winfo_width()
        main_height = self.master.winfo_height()
        main_x = self.master.winfo_x()
        main_y = self.master.winfo_y()

        center_x = main_x + (main_width // 2) - (dat_popup_width // 2)
        center_y = main_y + (main_height // 2) - (dat_popup_height // 2)

        screen_width = self.master.winfo_screenwidth()
        screen_height = self.master.winfo_screenheight()
        center_x = max(0, min(center_x, screen_width - dat_popup_width))
        center_y = max(0, min(center_y, screen_height - dat_popup_height))

        popup.geometry(f'{dat_popup_width}x{dat_popup_height}+{center_x}+{center_y}')
        # --- End position calculation ---

        # Make it modal AFTER positioning
        popup.grab_set()
        # Ensure popup is removed if closed via window manager
        popup.protocol("WM_DELETE_WINDOW", popup.destroy) # DAT creation doesn't have a simple cancel

        for index, game_data in enumerate(current_console_data):
            game_title = game_data.get('title', f'Unbekanntes Spiel ID {game_data.get("id")}') # Keep fallback or translate
            game_hashes = game_data.get('hashes', [])
            extended_info = game_data.get('extended_info')

            # Only include games that have at least one hash
            if not game_hashes or not isinstance(game_hashes, list) or len(game_hashes) == 0:
                 self.dat_progress_label_var.set(self.translate("dat_creation_skipping_no_hashes_progress", game_title[:40], index+1, total_games_in_dat)) # Use translated text
                 dat_progress_bar["value"] = index + 1
                 popup.update_idletasks()
                 self.master.update_idletasks()
                 continue

            games_with_hashes_count += 1
            if extended_info and extended_info.get('num_achievements', 0) > 0:
                 games_with_achievements_count += 1

            self.dat_progress_label_var.set(self.translate("dat_creation_processing_game_progress", game_title[:40], index+1, total_games_in_dat)) # Use translated text
            dat_progress_bar["value"] = index + 1
            popup.update_idletasks()
            self.master.update_idletasks()


            game_entry_lines = [
                "\tgame ("
            ]
            # Sanitize game title for DAT name/description
            game_title_sanitized = game_title.replace('"', "'").replace('&', 'and')
            game_entry_lines.append(f'\t\tname "{game_title_sanitized}"')
            game_entry_lines.append(f'\t\tdescription "{game_title_sanitized}"')

            # Add extended info as comment if available and requested
            comment_lines = []
            if extended_info:
                if self.include_achievements_var.get() and extended_info.get('num_achievements', 0) > 0:
                    comment_lines.append(self.translate('dat_comment_achievements', extended_info.get('num_achievements', 0), extended_info.get('points', 0))) # Add this key
                if self.include_patch_urls_var.get() and extended_info.get('patch_url'):
                    comment_lines.append(self.translate('dat_comment_patch_url', extended_info.get('patch_url'), extended_info.get('patch_md5', 'N/A'))) # Add this key

            if comment_lines:
                 # Join comment lines, escaping internal quotes if necessary
                 # Simple approach: replace " with ' inside comments
                 combined_comment = " | ".join(comment_lines).replace('"', "'")
                 game_entry_lines.append(f'\t\tcomment "{combined_comment}"')


            for file_hash_data in game_hashes:
                 if isinstance(file_hash_data, dict) and 'md5' in file_hash_data and file_hash_data.get('name'):
                    md5_hash = file_hash_data['md5']
                    filename = file_hash_data['name']
                    # Sanitize filename for DAT entry - remove problematic characters
                    allowed_chars_filename = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_.,()[]{}!@#$%^&\'~`+'
                    sanitized_filename = "".join(c for c in filename if c in allowed_chars_filename)
                    if not sanitized_filename: sanitized_filename = "unknown_file" # Fallback if sanitization results in empty string

                    # Example: Add size="0" since RA API doesn't provide it easily with this call
                    game_entry_lines.append("\t\trom (")
                    game_entry_lines.append(f'\t\t\tname "{sanitized_filename}"')
                    game_entry_lines.append(f'\t\t\tsize "0"') # Placeholder, size not available
                    game_entry_lines.append(f'\t\t\tcrc "00000000"') # Placeholder, CRC not available
                    game_entry_lines.append(f'\t\t\tmd5 "{md5_hash}"')
                    game_entry_lines.append("\t\t)") # End rom


            game_entry_lines.append("\t)") # End game
            game_entries_for_dat_file.extend(game_entry_lines)


        dat_filename = f"RetroAchievements - {console_name}.dat"
        full_output_path = os.path.join(dat_file_dir, dat_filename)

        try:
            # No newline='' here as per user request - keep OS default
            with open(full_output_path, "w", encoding="utf-8") as f: # Specify encoding
                for line in dat_header:
                    f.write(line + "\n") # Always write '\n' for line breaks in DAT
                for line in game_entries_for_dat_file:
                    f.write(line + "\n") # Always write '\n' for line breaks in DAT

            # --- Fortschrittsfenster schließen BEVOR die MessageBox kommt ---
            if hasattr(self, '_dat_progress_popup') and self._dat_progress_popup and tk.Toplevel.winfo_exists(self._dat_progress_popup):
                self._dat_progress_popup.destroy()
                self._dat_progress_popup = None
            # --- Ende Schließen ---

            messagebox.showinfo(self.translate("dat_creation_success_title"),
                                self.translate("dat_creation_success_text", dat_filename, os.path.abspath(full_output_path), games_with_hashes_count, games_with_achievements_count)) # Use translated text
            self.status_bar_text_var.set(self.translate("status_dat_created", dat_filename)) # Use translated text
            # print(f"DEBUG: DAT file created successfully with {games_with_hashes_count} games including hashes ({games_with_achievements_count} with achievements.)")

        except IOError as e:
            messagebox.showerror(self.translate("dat_creation_save_error_title"), self.translate("dat_creation_save_error_text", e)) # Use translated text
            self.status_bar_text_var.set(self.translate("status_dat_save_error")) # Use translated text
            # print(f"ERROR: Error writing DAT file: {e}")
        except Exception as e_gen:
            messagebox.showerror(self.translate("dat_creation_unexpected_error_title"), self.translate("dat_creation_unexpected_error_text", str(e_gen))) # Use translated text
            self.status_bar_text_var.set(self.translate("status_dat_unexpected_error")) # Use translated text
            # print(f"ERROR: Unexpected error creating DAT file: {e_gen}")
        finally:
             # Destroy progress popup here if it still exists (Fallback for errors)
             if hasattr(self, '_dat_progress_popup') and self._dat_progress_popup and tk.Toplevel.winfo_exists(self._dat_progress_popup):
                self._dat_progress_popup.destroy()
                self._dat_progress_popup = None
             self.on_selection_change(None)


    def create_retropie_collection(self):
        """Create a RetroPie custom collection file (.cfg) listing games with achievements."""
        console_name = self.selected_console_id_var.get()
         # Use the name-to-id map now
        console_id = self.console_name_to_id_map.get(console_name)
        system_short = self._get_system_short_name(console_name)

        if not system_short:
            messagebox.showerror(self.translate("collection_creation_shortname_error_title"), self.translate("collection_creation_shortname_error_text", console_name)) # Use translated text
            self.on_selection_change(None)
            return

        retropie_rom_base = self.retropie_base_path.get().strip()
        # Angepasst: Nur prüfen, ob der Pfad gesetzt ist, nicht ob er lokal existiert
        if not retropie_rom_base:
             # Updated error message key
             messagebox.showerror(self.translate("collection_creation_no_retropie_path_error_title"), self.translate("collection_creation_no_retropie_path_error_text")) # Use translated text
             self.on_selection_change(None)
             return

        # This path is used inside the CFG file, so it needs to be the RetroPie specific one
        collection_rom_base_path = retropie_rom_base

        retropie_system_rom_path = os.path.join(collection_rom_base_path, system_short)
        collection_cfg_dir = self.collection_cfg_save_path.get()


        if not console_id:
            messagebox.showerror(self.translate("collection_creation_invalid_console_error_title"), self.translate("collection_creation_invalid_console_error_text")) # Use translated text
            self.on_selection_change(None)
            return
        if not collection_cfg_dir or not os.path.isdir(collection_cfg_dir):
            messagebox.showerror(self.translate("collection_creation_invalid_save_path_error_title"), self.translate("collection_creation_invalid_save_path_error_text")) # Use translated text
            self.on_selection_change(None)
            return

        # Ensure console_id is string for data access
        console_id_str = str(console_id)

        current_console_data = self.cached_data.get(console_id_str) # Use string ID
         # Caching is always on, try loading from cache if not in memory
        if current_console_data is None or not current_console_data:
            current_console_data = self.load_from_cache(console_id_str) # Use string ID
            if current_console_data is not None and isinstance(current_console_data, list):
                 self.cached_data[console_id_str] = current_console_data # Load into memory using string ID
            else:
                 messagebox.showwarning(self.translate("collection_creation_no_data_warning_title"), self.translate("collection_creation_no_data_warning_text", console_name)) # Use translated text
                 self.on_selection_change(None)
                 return


        if not current_console_data: # Check again after attempting to load from cache
            messagebox.showinfo(self.translate("collection_creation_no_data_info_title"), self.translate("collection_creation_no_data_info_text", console_name)) # Use translated text
            self.status_bar_text_var.set(self.translate("status_no_data_for_collection", console_name)) # Use translated text
            self.on_selection_change(None)
            return

        games_with_achievements = []
        for game_data in current_console_data:
            extended_info = game_data.get('extended_info')
            # Only include if game has achievements AND has hashes (meaning it's processable)
            if extended_info and extended_info.get('num_achievements', 0) > 0 and game_data.get('hashes'):
                 games_with_achievements.append(game_data)


        if not games_with_achievements:
             messagebox.showinfo(self.translate("collection_no_achievements_info_title"), self.translate("collection_no_achievements_info_text", console_name)) # Use translated text
             self.status_bar_text_var.set(self.translate("status_no_achievements_with_hashes", console_name)) # Use translated text
             self.on_selection_change(None)
             return

        self.status_bar_text_var.set(self.translate("status_collection_creation_start", console_name)) # Use translated text
        # Disable all creation buttons during process
        self.create_dat_button.config(state="disabled")
        self.fetch_data_button.config(state="disabled")
        self.create_retropie_collection_button.config(state="disabled") # Disable this one too
        self.create_batocera_collection_button.config(state="disabled") # Disable the other one too

        self.master.update_idletasks()

        # Use collection-specific progress popup variable (re-use the same popup)
        self._collection_progress_popup = tk.Toplevel(self.master) # Store reference
        popup = self._collection_progress_popup # Use local name for convenience
        # Use generic title for both collection types
        popup.title(self.translate("collection_creation_progress_title")) # Use translated text
        # Set initial size, position calculated later
        collection_popup_width = 450
        collection_popup_height = 130
        popup.geometry(f"{collection_popup_width}x{collection_popup_height}")
        popup.resizable(False, False)


        # Use collection-specific label variable
        self.collection_progress_label_var.set(self.translate("status_collection_creation_start", console_name)) # Use translated text
        ttk.Label(popup, textvariable=self.collection_progress_label_var, wraplength=430).pack(pady=10, padx=10, fill="x")
        progress_bar = ttk.Progressbar(popup, orient="horizontal", length=430, mode="determinate")
        progress_bar.pack(pady=10, padx=10)
        progress_bar["maximum"] = len(games_with_achievements)

        # --- Calculate and set collection popup position ---
        self.master.update_idletasks() # Ensure main window geometry is up-to-date
        main_width = self.master.winfo_width()
        main_height = self.master.winfo_height()
        main_x = self.master.winfo_x()
        main_y = self.master.winfo_y()

        center_x = main_x + (main_width // 2) - (collection_popup_width // 2)
        center_y = main_y + (main_height // 2) - (collection_popup_height // 2)

        screen_width = self.master.winfo_screenwidth()
        screen_height = self.master.winfo_screenheight()
        center_x = max(0, min(center_x, screen_width - collection_popup_width))
        center_y = max(0, min(center_y, screen_height - collection_popup_height))

        popup.geometry(f'{collection_popup_width}x{collection_popup_height}+{center_x}+{center_y}')
        # --- End position calculation ---

        # Make it modal AFTER positioning
        popup.grab_set()
        # Ensure popup is removed if closed via window manager
        popup.protocol("WM_DELETE_WINDOW", popup.destroy) # Collection creation doesn't have a simple cancel


        collection_filename = f"custom-RetroAchievements-{system_short}.cfg"
        full_output_path = os.path.join(collection_cfg_dir, collection_filename)
        games_added_to_cfg = 0

        try:
            # print(f"DEBUG: Writing RetroPie Collection file to: {full_output_path}")
            # print(f"DEBUG: Using base ROM path for entries in CFG: {retropie_system_rom_path}")

            # Get the desired extension from the variable
            desired_extension = self.rom_extension_var.get().strip()
            if not desired_extension.startswith('.'):
                 desired_extension = '.' + desired_extension # Ensure it starts with a dot

            # ### MODIFIED: Add newline='' to ensure LF line endings for CFG file ###
            with open(full_output_path, "w", encoding="utf-8", newline='') as f: # Specify encoding and newline
                for index, game_data in enumerate(games_with_achievements):
                    game_title = game_data.get('title', f'Unbekanntes Spiel ID {game_data.get("id")}') # Keep fallback or translate
                    game_hashes = game_data.get('hashes', [])
                    self.collection_progress_label_var.set(self.translate("collection_creation_adding_game", game_title[:40], index+1, len(games_with_achievements))) # Use translated text
                    progress_bar["value"] = index + 1
                    popup.update_idletasks()
                    self.master.update_idletasks()

                    rom_filename = ""
                    # Prioritize filename from hash data if available
                    if game_hashes and isinstance(game_hashes, list):
                         # Find the first hash entry with a filename
                         for hash_entry in game_hashes:
                             if isinstance(hash_entry, dict) and hash_entry.get('name'):
                                 rom_filename = hash_entry['name']
                                 # print(f"DEBUG: Using filename from hash entry: {rom_filename}")
                                 break # Use the first found filename
                    # If no filename in hash data, generate a fallback
                    if not rom_filename:
                        game_title_sanitized = game_title.replace('"', "'").replace('&', 'and')
                        allowed_chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_.,()[]:\'#&!+'
                        game_title_sanitized = "".join(c for c in game_title_sanitized if c in allowed_chars).strip()
                        rom_name_base = "".join(c for c in game_title_sanitized if c.isalnum() or c in ' _-').strip()
                        rom_name_base = rom_name_base.replace(" ", "_")
                        if not rom_name_base: rom_name_base = f"game_{game_data.get('id', index)}" # Keep fallback or translate
                        # OLD: rom_ext = self._get_typical_extension(console_name)
                        # OLD: rom_filename = f"{rom_name_base}.{rom_ext}"
                        # NEW: Use the determined rom_name_base without an extension yet
                        rom_filename = rom_name_base
                        # print(f"DEBUG: Generating fallback filename base: {rom_filename}")


                    # ### MODIFIED: Use the value from the new rom_extension_var ###
                    # Check if the filename already has an extension that matches the desired one
                    base_name, existing_ext = os.path.splitext(rom_filename)
                    if existing_ext.lower() != desired_extension.lower():
                         # If it doesn't match, replace or add the desired extension
                         rom_filename = base_name + desired_extension
                    # else: The existing extension matches, use the filename as is.

                    # print(f"DEBUG: Using final filename with desired extension: {rom_filename}")


                    rom_path_in_cfg = f"{os.path.normpath(retropie_system_rom_path).replace(os.sep, '/')}/{os.path.normpath(rom_filename).replace(os.sep, '/')}"

                    f.write(rom_path_in_cfg + "\n")
                    games_added_to_cfg += 1
                    # print(f"DEBUG: Added to CFG: {rom_path_in_cfg}")

            # Destroy progress popup after creation loop - MOVED to finally block

            # --- Fortschrittsfenster schließen BEVOR die MessageBox kommt ---
            if hasattr(self, '_collection_progress_popup') and self._collection_progress_popup and tk.Toplevel.winfo_exists(self._collection_progress_popup):
                self._collection_progress_popup.destroy()
                self._collection_progress_popup = None
            # --- Ende Schließen ---

            # Updated success message key (generic)
            messagebox.showinfo(self.translate("collection_creation_success_title"),
                self.translate("collection_creation_success_text", collection_filename, os.path.abspath(full_output_path), games_added_to_cfg)) # Use translated text
            self.status_bar_text_var.set(self.translate("status_collection_created", collection_filename)) # Use translated text
            # print(f"DEBUG: Collection file created successfully with {games_added_to_cfg} entries.)

        except IOError as e:
            messagebox.showerror(self.translate("collection_creation_save_error_title"), self.translate("collection_creation_save_error_text", e)) # Use translated text
            self.status_bar_text_var.set(self.translate("status_collection_save_error")) # Use translated text
            # print(f"ERROR: Error writing collection file: {e}")
        except Exception as e_gen:
            messagebox.showerror(self.translate("collection_creation_unexpected_error_title"), self.translate("collection_creation_unexpected_error_text", str(e_gen))) # Use translated text
            self.status_bar_text_var.set(self.translate("status_collection_unexpected_error")) # Use translated text
            # print(f"ERROR: Unexpected error creating collection file: {e_gen}")
        finally:
             # Destroy progress popup here if it still exists (Fallback for errors)
             if hasattr(self, '_collection_progress_popup') and self._collection_progress_popup and tk.Toplevel.winfo_exists(self._collection_progress_popup):
                self._collection_progress_popup.destroy()
                self._collection_progress_popup = None
             self.on_selection_change(None)


    def create_batocera_collection(self):
        """Create a Batocera custom collection file (.cfg) listing games with achievements."""
        console_name = self.selected_console_id_var.get()
         # Use the name-to-id map now
        console_id = self.console_name_to_id_map.get(console_name)
        system_short = self._get_system_short_name(console_name)

        if not system_short:
            messagebox.showerror(self.translate("collection_creation_shortname_error_title"), self.translate("collection_creation_shortname_error_text", console_name)) # Use translated text
            self.on_selection_change(None)
            return

        batocera_rom_base = self.batocera_base_path.get().strip()
        # Check if the path is set for Batocera
        if not batocera_rom_base:
             # Updated error message key for Batocera
             messagebox.showerror(self.translate("collection_creation_no_batocera_path_error_title"), self.translate("collection_creation_no_batocera_path_error_text")) # Add this new key
             self.on_selection_change(None)
             return

        # This path is used inside the CFG file, so it needs to be the Batocera specific one
        collection_rom_base_path = batocera_rom_base

        batocera_system_rom_path = os.path.join(collection_rom_base_path, system_short)
        collection_cfg_dir = self.collection_cfg_save_path.get()


        if not console_id:
            messagebox.showerror(self.translate("collection_creation_invalid_console_error_title"), self.translate("collection_creation_invalid_console_error_text")) # Use translated text
            self.on_selection_change(None)
            return
        if not collection_cfg_dir or not os.path.isdir(collection_cfg_dir):
            messagebox.showerror(self.translate("collection_creation_invalid_save_path_error_title"), self.translate("collection_creation_invalid_save_path_error_text")) # Use translated text
            self.on_selection_change(None)
            return

        # Ensure console_id is string for data access
        console_id_str = str(console_id)

        current_console_data = self.cached_data.get(console_id_str) # Use string ID
         # Caching is always on, try loading from cache if not in memory
        if current_console_data is None or not current_console_data:
            current_console_data = self.load_from_cache(console_id_str) # Use string ID
            if current_console_data is not None and isinstance(current_console_data, list):
                 self.cached_data[console_id_str] = current_console_data # Load into memory using string ID
            else:
                 messagebox.showwarning(self.translate("collection_creation_no_data_warning_title"), self.translate("collection_creation_no_data_warning_text", console_name)) # Use translated text
                 self.on_selection_change(None)
                 return


        if not current_console_data: # Check again after attempting to load from cache
            messagebox.showinfo(self.translate("collection_creation_no_data_info_title"), self.translate("collection_creation_no_data_info_text", console_name)) # Use translated text
            self.status_bar_text_var.set(self.translate("status_no_data_for_collection", console_name)) # Use translated text
            self.on_selection_change(None)
            return

        games_with_achievements = []
        for game_data in current_console_data:
            extended_info = game_data.get('extended_info')
            # Only include if game has achievements AND has hashes (meaning it's processable)
            if extended_info and extended_info.get('num_achievements', 0) > 0 and game_data.get('hashes'):
                 games_with_achievements.append(game_data)


        if not games_with_achievements:
             messagebox.showinfo(self.translate("collection_no_achievements_info_title"), self.translate("collection_no_achievements_info_text", console_name)) # Use translated text
             self.status_bar_text_var.set(self.translate("status_no_achievements_with_hashes", console_name)) # Use translated text
             self.on_selection_change(None)
             return


        # Updated status message key for Batocera
        self.status_bar_text_var.set(self.translate("status_batocera_collection_creation_start", console_name)) # Add this new key
        # Disable all creation buttons during process
        self.create_dat_button.config(state="disabled")
        self.fetch_data_button.config(state="disabled")
        self.create_retropie_collection_button.config(state="disabled")
        self.create_batocera_collection_button.config(state="disabled") # Disable this one too

        self.master.update_idletasks()

        # Use collection-specific progress popup variable (re-use the same popup)
        self._collection_progress_popup = tk.Toplevel(self.master) # Store reference
        popup = self._collection_progress_popup # Use local name for convenience
        # Updated progress title key for Batocera
        popup.title(self.translate("batocera_collection_creation_progress_title")) # Add this new key
        # Set initial size, position calculated later
        collection_popup_width = 450
        collection_popup_height = 130
        popup.geometry(f"{collection_popup_width}x{collection_popup_height}")
        popup.resizable(False, False)


        # Use collection-specific label variable
        self.collection_progress_label_var.set(self.translate("status_batocera_collection_creation_start", console_name)) # Use the new key
        ttk.Label(popup, textvariable=self.collection_progress_label_var, wraplength=430).pack(pady=10, padx=10, fill="x")
        progress_bar = ttk.Progressbar(popup, orient="horizontal", length=430, mode="determinate")
        progress_bar.pack(pady=10, padx=10)
        progress_bar["maximum"] = len(games_with_achievements)

        # --- Calculate and set collection popup position ---
        self.master.update_idletasks() # Ensure main window geometry is up-to-date
        main_width = self.master.winfo_width()
        main_height = self.master.winfo_height()
        main_x = self.master.winfo_x()
        main_y = self.master.winfo_y()

        center_x = main_x + (main_width // 2) - (collection_popup_width // 2)
        center_y = main_y + (main_height // 2) - (collection_popup_height // 2)

        screen_width = self.master.winfo_screenwidth()
        screen_height = self.master.winfo_screenheight()
        center_x = max(0, min(center_x, screen_width - collection_popup_width))
        center_y = max(0, min(center_y, screen_height - collection_popup_height))

        popup.geometry(f'{collection_popup_width}x{collection_popup_height}+{center_x}+{center_y}')
        # --- End position calculation ---

        # Make it modal AFTER positioning
        popup.grab_set()
        # Ensure popup is removed if closed via window manager
        popup.protocol("WM_DELETE_WINDOW", popup.destroy) # Collection creation doesn't have a simple cancel


        # Renamed filename for clarity (Batocera specific)
        collection_filename = f"custom-RetroAchievements-{system_short}-batocera.cfg" # Added -batocera
        full_output_path = os.path.join(collection_cfg_dir, collection_filename)
        games_added_to_cfg = 0

        try:
            # print(f"DEBUG: Writing Batocera Collection file to: {full_output_path}")
            # print(f"DEBUG: Using base ROM path for entries in CFG: {batocera_system_rom_path}")

            # Get the desired extension from the variable
            desired_extension = self.rom_extension_var.get().strip()
            if not desired_extension.startswith('.'):
                 desired_extension = '.' + desired_extension # Ensure it starts with a dot


            # ### MODIFIED: Add newline='' to ensure LF line endings for CFG file ###
            with open(full_output_path, "w", encoding="utf-8", newline='') as f: # Specify encoding and newline
                for index, game_data in enumerate(games_with_achievements):
                    game_title = game_data.get('title', f'Unbekanntes Spiel ID {game_data.get("id")}') # Keep fallback or translate
                    game_hashes = game_data.get('hashes', [])
                    # Updated progress message key for Batocera
                    self.collection_progress_label_var.set(self.translate("batocera_collection_creation_adding_game", game_title[:40], index+1, len(games_with_achievements))) # Add this new key
                    progress_bar["value"] = index + 1
                    popup.update_idletasks()
                    self.master.update_idletasks()

                    rom_filename = ""
                    # Prioritize filename from hash data if available
                    if game_hashes and isinstance(game_hashes, list):
                         # Find the first hash entry with a filename
                         for hash_entry in game_hashes:
                             if isinstance(hash_entry, dict) and hash_entry.get('name'):
                                 rom_filename = hash_entry['name']
                                 # print(f"DEBUG: Using filename from hash entry: {rom_filename}")
                                 break # Use the first found filename
                    # If no filename in hash data, generate a fallback
                    if not rom_filename:
                        game_title_sanitized = game_title.replace('"', "'").replace('&', 'and')
                        allowed_chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_.,()[]:\'#&!+'
                        game_title_sanitized = "".join(c for c in game_title_sanitized if c in allowed_chars).strip()
                        rom_name_base = "".join(c for c in game_title_sanitized if c.isalnum() or c in ' _-').strip()
                        rom_name_base = rom_name_base.replace(" ", "_")
                        if not rom_name_base: rom_name_base = f"game_{game_data.get('id', index)}" # Keep fallback or translate
                        # OLD: rom_ext = self._get_typical_extension(console_name) # No longer needed here
                        # OLD: rom_filename = f"{rom_name_base}.{rom_ext}"
                        # NEW: Use the determined rom_name_base without an extension yet
                        rom_filename = rom_name_base
                        # print(f"DEBUG: Generating fallback filename base: {rom_filename}")


                    # ### MODIFIED: Use the value from the new rom_extension_var ###
                    # Check if the filename already has an extension that matches the desired one
                    base_name, existing_ext = os.path.splitext(rom_filename)
                    if existing_ext.lower() != desired_extension.lower():
                         # If it doesn't match, replace or add the desired extension
                         rom_filename = base_name + desired_extension
                    # else: The existing extension matches, use the filename as is.

                    # print(f"DEBUG: Using final filename with desired extension: {rom_filename}")


                    rom_path_in_cfg = f"{os.path.normpath(batocera_system_rom_path).replace(os.sep, '/')}/{os.path.normpath(rom_filename).replace(os.sep, '/')}"

                    f.write(rom_path_in_cfg + "\n")
                    games_added_to_cfg += 1
                    # print(f"DEBUG: Added to CFG: {rom_path_in_cfg}")

            # Destroy progress popup after creation loop - MOVED to finally block

            # --- Fortschrittsfenster schließen BEVOR die MessageBox kommt ---
            if hasattr(self, '_collection_progress_popup') and self._collection_progress_popup and tk.Toplevel.winfo_exists(self._collection_progress_popup):
                self._collection_progress_popup.destroy()
                self._collection_progress_popup = None
            # --- Ende Schließen ---

            # Updated success message key for Batocera
            messagebox.showinfo(self.translate("collection_creation_success_title"), # Re-use same title
                self.translate("batocera_collection_creation_success_text", collection_filename, os.path.abspath(full_output_path), games_added_to_cfg)) # Add this new key
            # Updated status message key for Batocera
            self.status_bar_text_var.set(self.translate("status_batocera_collection_created", collection_filename)) # Add this new key
            # print(f"DEBUG: Batocera Collection file created successfully with {games_added_to_cfg} entries.)

        except IOError as e:
            messagebox.showerror(self.translate("collection_creation_save_error_title"), self.translate("collection_creation_save_error_text", e)) # Use translated text
            self.status_bar_text_var.set(self.translate("status_collection_save_error")) # Use translated text
            # print(f"ERROR: Error writing collection file: {e}")
        except Exception as e_gen:
            messagebox.showerror(self.translate("collection_creation_unexpected_error_title"), self.translate("collection_creation_unexpected_error_text", str(e_gen))) # Use translated text
            self.status_bar_text_var.set(self.translate("status_collection_unexpected_error")) # Use translated text
            # print(f"ERROR: Unexpected error creating collection file: {e_gen}")
        finally:
             # Destroy progress popup here if it still exists (Fallback for errors)
             if hasattr(self, '_collection_progress_popup') and self._collection_progress_popup and tk.Toplevel.winfo_exists(self._collection_progress_popup):
                self._collection_progress_popup.destroy()
                self._collection_progress_popup = None
             self.on_selection_change(None)


def main():
    """Main function to initialize and run the Tkinter application."""
    root = None # Ensure root is defined before try block in case of very early error
    try:
        root = tk.Tk()
        window_width = 650
        window_height = 750
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        center_x = int(screen_width/2 - window_width / 2)
        center_y = int(screen_height/2 - window_height / 2)
        center_x = max(0, center_x)
        center_y = max(0, center_y)
        root.geometry(f'{window_width}x{window_height}+{center_x}+{center_y}')
        root.minsize(window_width, window_height) # Set minsize to initial size

        app = RetroAchievementsDATGenerator(root)
        root.mainloop()
    except Exception as e:
        import traceback
        error_message = f"Ein kritischer Fehler ist in der Anwendung aufgetreten:\n\n{str(e)}\n\n{traceback.format_exc()}"
        print(error_message)
        try:
            # Try to use Tkinter messagebox if root was successfully created, otherwise just print
            if root and tk.Tk.winfo_exists(root):
                # Check if app instance was created and translations are loaded
                if 'app' in locals() and hasattr(app, 'translations') and 'critical_error_title' in app.translations:
                     translated_title = app.translate("critical_error_title")
                     translated_text = app.translate("critical_error_text", str(e), traceback.format_exc())
                     messagebox.showerror(translated_title, translated_text, parent=None)
                else:
                     # Fallback if app/translations not available
                     messagebox.showerror("Critical Error", error_message, parent=None)
            else:
                 # If root wasn't even created, print the error (already done above)
                 pass

        except Exception as tk_error:
            print(f"Konnte Tkinter-Fehlermeldung nicht anzeigen: {tk_error}")


if __name__ == '__main__':
    main()
