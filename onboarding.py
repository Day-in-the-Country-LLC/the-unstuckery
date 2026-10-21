"""Remember this Mac's Drive path; saved application memory lives in Drive."""
import os
from pathlib import Path
from drive_storage import MARKER, StorageError, read_json, write_json


def load_location(support):
    path = Path(support) / 'storage.json'
    if not path.exists():
        return None
    value = read_json(path)
    if not isinstance(value.get('folder'), str) or not Path(value['folder']).is_absolute():
        raise StorageError('Choose your Google Drive folder again in Student’s storage setup.')
    return value


def memory_folder(selected):
    selected = Path(selected).expanduser().resolve()
    if (selected / MARKER).exists() or selected.name == 'Student App Memory':
        return selected
    return selected / 'Student App Memory'


def legacy_data(selected, support):
    selected = Path(selected)
    for candidate in (selected, selected / 'data', selected / 'river-after-school-app/data', Path(support) / 'data'):
        if (candidate / 'river.sqlite3').is_file():
            return candidate
    return None


def choose_folder(title, prompt):
    from AppKit import NSApplication, NSOpenPanel, NSModalResponseOK
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    panel = NSOpenPanel.openPanel()
    panel.setTitle_(title)
    panel.setMessage_(prompt)
    panel.setPrompt_('Choose folder')
    panel.setCanChooseDirectories_(True)
    panel.setCanChooseFiles_(False)
    panel.setCanCreateDirectories_(True)
    panel.setAllowsMultipleSelection_(False)
    if panel.runModal() != NSModalResponseOK:
        return None
    return Path(panel.URL().path())


def configure(support):
    import rumps
    selected = choose_folder('Where should Student remember everything?',
        'Choose a folder in Google Drive for desktop. On every laptop, choose this same folder. Student will keep saved progress, photos, your parent PIN, and connection settings in Student App Memory inside it.')
    if selected is None:
        return None
    folder = memory_folder(selected)
    seed = None
    if not (folder / MARKER).exists():
        seed = legacy_data(selected, support)
        if seed:
            if not rumps.alert('Bring your existing Student progress?',
                f'River found saved progress in:\n{seed}\n\nQuit the old River app first. Your original files will be kept. Connection settings on this Mac will also be copied into your private Drive folder.',
                ok='Use this progress', cancel='Cancel'):
                return None
        else:
            if rumps.alert('Continue existing progress or start fresh?',
                'If you already use Student, choose its old data folder to bring your history and photos. Start fresh creates an empty school plan.',
                ok='Choose existing data', cancel='Start fresh'):
                seed = choose_folder('Choose existing Student data', 'Choose the folder containing river.sqlite3. Quit the old Student app before continuing.')
                if seed is None:
                    return None
                if not (seed / 'river.sqlite3').is_file():
                    raise StorageError('That folder does not contain river.sqlite3. Choose the old app’s data folder.')
        folder.mkdir(exist_ok=True, mode=0o700)
    value = {'folder': str(folder), 'create': not (folder / MARKER).exists()}
    if seed:
        value['seed'] = str(seed)
    Path(support).mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(Path(support) / 'storage.json', value)
    return value
