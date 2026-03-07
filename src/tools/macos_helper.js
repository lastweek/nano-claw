ObjC.import("Foundation");
ObjC.import("AppKit");

function ok(data) {
  return {ok: true, data: data};
}

function err(code, message) {
  return {ok: false, error: {code: code, message: message}};
}

function unwrap(value) {
  if (value === undefined || value === null) {
    return null;
  }
  try {
    return ObjC.deepUnwrap(value);
  } catch (error) {
    try {
      return ObjC.unwrap(value);
    } catch (innerError) {
      return value;
    }
  }
}

function toJsArray(value) {
  if (value === undefined || value === null) {
    return [];
  }
  if (value.count !== undefined && value.objectAtIndex) {
    const result = [];
    const count = Number(typeof value.count === "function" ? value.count() : value.count);
    for (let index = 0; index < count; index += 1) {
      result.push(String(unwrap(value.objectAtIndex(index))));
    }
    return result;
  }
  try {
    return ObjC.unwrap(value);
  } catch (error) {
    return [];
  }
}

function readInput() {
  const data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
  if (!data || data.length === 0) {
    return {};
  }
  const text = unwrap($.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding)) || "";
  return JSON.parse(String(text));
}

function fileManager() {
  return $.NSFileManager.defaultManager;
}

function fileUrl(path) {
  return $.NSURL.fileURLWithPath(path);
}

function ensureExists(path) {
  const isDirectory = Ref();
  const exists = fileManager().fileExistsAtPathIsDirectory(path, isDirectory);
  if (!exists) {
    throw {code: "not_found", message: "No item found at " + path};
  }
  return Boolean(isDirectory[0]);
}

function listFinderItems(args) {
  const targetPath = String(args.path || "");
  if (!targetPath) {
    throw {code: "validation_error", message: "path is required"};
  }

  const isDirectory = ensureExists(targetPath);
  if (!isDirectory) {
    throw {code: "validation_error", message: "list_items requires a directory path"};
  }

  const names = toJsArray(fileManager().contentsOfDirectoryAtPathError(targetPath, null));
  const includeHidden = Boolean(args.include_hidden);
  const limit = Number(args.limit || 200);
  const items = [];

  for (let index = 0; index < names.length && items.length < limit; index += 1) {
    const name = String(names[index]);
    if (!includeHidden && name.startsWith(".")) {
      continue;
    }
    const fullPath = unwrap(
      $.NSString.stringWithString(targetPath).stringByAppendingPathComponent(name)
    );
    const childIsDirectory = Ref();
    fileManager().fileExistsAtPathIsDirectory(fullPath, childIsDirectory);
    items.push({
      name: name,
      path: String(fullPath),
      kind: Boolean(childIsDirectory[0]) ? "Folder" : "File",
      is_directory: Boolean(childIsDirectory[0]),
    });
  }

  return {items: items};
}

function openFinderItem(args) {
  const targetPath = String(args.path || "");
  ensureExists(targetPath);
  const opened = $.NSWorkspace.sharedWorkspace.openURL(fileUrl(targetPath));
  if (!opened) {
    throw {code: "script_error", message: "Failed to open item in Finder"};
  }
  return {path: targetPath, opened: true};
}

function revealFinderItem(args) {
  const targetPath = String(args.path || "");
  ensureExists(targetPath);
  const urls = $.NSArray.arrayWithObject(fileUrl(targetPath));
  $.NSWorkspace.sharedWorkspace.activateFileViewerSelectingURLs(urls);
  return {path: targetPath, revealed: true};
}

function createFinderFolder(args) {
  const parentPath = String(args.parent_path || "");
  const folderName = String(args.name || "");
  if (!parentPath || !folderName) {
    throw {code: "validation_error", message: "parent_path and name are required"};
  }
  const targetPath = unwrap(
    $.NSString.stringWithString(parentPath).stringByAppendingPathComponent(folderName)
  );
  const created = fileManager().createDirectoryAtPathWithIntermediateDirectoriesAttributesError(
    targetPath,
    true,
    null,
    null
  );
  if (!created) {
    throw {code: "script_error", message: "Failed to create folder at " + targetPath};
  }
  return {
    name: folderName,
    path: String(targetPath),
    kind: "Folder",
    is_directory: true,
  };
}

function renameFinderItem(args) {
  const targetPath = String(args.path || "");
  const newName = String(args.new_name || "");
  if (!targetPath || !newName) {
    throw {code: "validation_error", message: "path and new_name are required"};
  }
  ensureExists(targetPath);
  const parentPath = unwrap($.NSString.stringWithString(targetPath).stringByDeletingLastPathComponent);
  const newPath = unwrap($.NSString.stringWithString(parentPath).stringByAppendingPathComponent(newName));
  const moved = fileManager().moveItemAtPathToPathError(targetPath, newPath, null);
  if (!moved) {
    throw {code: "script_error", message: "Failed to rename item to " + newName};
  }
  const renamedIsDirectory = Ref();
  fileManager().fileExistsAtPathIsDirectory(newPath, renamedIsDirectory);
  return {
    name: newName,
    path: String(newPath),
    kind: Boolean(renamedIsDirectory[0]) ? "Folder" : "File",
    is_directory: Boolean(renamedIsDirectory[0]),
  };
}

function safeCall(target, methodName) {
  if (!target || typeof target[methodName] !== "function") {
    return null;
  }
  try {
    return target[methodName]();
  } catch (error) {
    return null;
  }
}

function toIso(value) {
  if (!value) {
    return null;
  }
  try {
    return new Date(value).toISOString();
  } catch (error) {
    return null;
  }
}

function padNumber(value) {
  return String(value).padStart(2, "0");
}

function formatDateOnly(value) {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return [
    String(date.getFullYear()),
    padNumber(date.getMonth() + 1),
    padNumber(date.getDate()),
  ].join("-");
}

function parseDateOnly(value) {
  const text = String(value || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    throw {code: "validation_error", message: "due_on must be a valid YYYY-MM-DD date"};
  }
  const parts = text.split("-").map(Number);
  return new Date(parts[0], parts[1] - 1, parts[2], 12, 0, 0, 0);
}

function parseIsoDate(value, fieldName) {
  const date = new Date(String(value || ""));
  if (Number.isNaN(date.getTime())) {
    throw {code: "validation_error", message: fieldName + " must be a valid ISO 8601 datetime"};
  }
  return date;
}

function flattenNotesFolders(app) {
  const accounts = safeCall(app, "accounts") || [];
  const folders = [];
  for (let accountIndex = 0; accountIndex < accounts.length; accountIndex += 1) {
    const accountFolders = safeCall(accounts[accountIndex], "folders") || [];
    for (let folderIndex = 0; folderIndex < accountFolders.length; folderIndex += 1) {
      folders.push(accountFolders[folderIndex]);
    }
  }
  return folders;
}

function noteToRecord(note, folderName) {
  const body = String(safeCall(note, "body") || "");
  const plainBody = body.replace(/<[^>]+>/g, "").trim();
  return {
    note_id: String(safeCall(note, "id") || ""),
    title: String(safeCall(note, "name") || ""),
    folder_name: folderName,
    body_text: plainBody,
    created_at: toIso(safeCall(note, "creationDate")),
    updated_at: toIso(safeCall(note, "modificationDate")),
    snippet: plainBody.slice(0, 160),
  };
}

function allNotes(app) {
  const folders = flattenNotesFolders(app);
  const records = [];
  for (let folderIndex = 0; folderIndex < folders.length; folderIndex += 1) {
    const folder = folders[folderIndex];
    const folderName = String(safeCall(folder, "name") || "");
    const notes = safeCall(folder, "notes") || [];
    for (let noteIndex = 0; noteIndex < notes.length; noteIndex += 1) {
      records.push({folder: folder, folderName: folderName, note: notes[noteIndex]});
    }
  }
  return records;
}

function findNotesFolder(app, folderName) {
  const folders = flattenNotesFolders(app);
  for (let index = 0; index < folders.length; index += 1) {
    if (String(safeCall(folders[index], "name") || "") === folderName) {
      return folders[index];
    }
  }
  throw {code: "not_found", message: "No Notes folder named " + folderName};
}

function runNotes(action, args) {
  const notes = Application("Notes");
  notes.includeStandardAdditions = true;

  if (action === "list_notes") {
    const records = allNotes(notes);
    const query = String(args.query || "").toLowerCase();
    const folderName = args.folder_name ? String(args.folder_name) : null;
    const limit = Number(args.limit || 100);
    const filtered = [];
    for (let index = 0; index < records.length && filtered.length < limit; index += 1) {
      const record = records[index];
      if (folderName && record.folderName !== folderName) {
        continue;
      }
      const mapped = noteToRecord(record.note, record.folderName);
      if (query) {
        const haystack = (mapped.title + "\n" + mapped.body_text).toLowerCase();
        if (haystack.indexOf(query) === -1) {
          continue;
        }
      }
      filtered.push(mapped);
    }
    return {notes: filtered};
  }

  if (action === "read_note") {
    const noteId = String(args.note_id || "");
    const records = allNotes(notes);
    for (let index = 0; index < records.length; index += 1) {
      const mapped = noteToRecord(records[index].note, records[index].folderName);
      if (mapped.note_id === noteId) {
        return mapped;
      }
    }
    throw {code: "not_found", message: "No note found for note_id " + noteId};
  }

  if (action === "create_note") {
    const folder = findNotesFolder(notes, String(args.folder_name || ""));
    const newNote = notes.Note({
      name: String(args.title || ""),
      body: String(args.body_text || ""),
    });
    folder.notes.push(newNote);
    return noteToRecord(newNote, String(args.folder_name || ""));
  }

  if (action === "update_note") {
    const noteId = String(args.note_id || "");
    const records = allNotes(notes);
    for (let index = 0; index < records.length; index += 1) {
      const record = records[index];
      const currentId = String(safeCall(record.note, "id") || "");
      if (currentId !== noteId) {
        continue;
      }
      if (args.title) {
        record.note.name = String(args.title);
      }
      if (args.body_text) {
        const currentBody = String(safeCall(record.note, "body") || "");
        record.note.body = args.body_mode === "append"
          ? currentBody + "\n" + String(args.body_text)
          : String(args.body_text);
      }
      return noteToRecord(record.note, record.folderName);
    }
    throw {code: "not_found", message: "No note found for note_id " + noteId};
  }

  throw {code: "validation_error", message: "Unsupported Notes action: " + action};
}

function calendarRecord(calendar) {
  return {name: String(safeCall(calendar, "name") || "")};
}

function calendarEventRecord(event, calendarName) {
  return {
    event_id: String(safeCall(event, "id") || ""),
    calendar_name: calendarName,
    title: String(safeCall(event, "summary") || safeCall(event, "name") || ""),
    start_at: toIso(safeCall(event, "startDate")),
    end_at: toIso(safeCall(event, "endDate")),
    location: String(safeCall(event, "location") || ""),
    notes: String(safeCall(event, "description") || ""),
  };
}

function allCalendarEvents(app) {
  const calendars = safeCall(app, "calendars") || [];
  const records = [];
  for (let calendarIndex = 0; calendarIndex < calendars.length; calendarIndex += 1) {
    const calendar = calendars[calendarIndex];
    const calendarName = String(safeCall(calendar, "name") || "");
    const events = safeCall(calendar, "events") || [];
    for (let eventIndex = 0; eventIndex < events.length; eventIndex += 1) {
      records.push({
        calendar: calendar,
        calendarName: calendarName,
        event: events[eventIndex],
      });
    }
  }
  return records;
}

function findCalendar(app, calendarName) {
  const calendars = safeCall(app, "calendars") || [];
  for (let index = 0; index < calendars.length; index += 1) {
    if (String(safeCall(calendars[index], "name") || "") === calendarName) {
      return calendars[index];
    }
  }
  throw {code: "not_found", message: "No calendar named " + calendarName};
}

function runCalendar(action, args) {
  const calendarApp = Application("Calendar");
  calendarApp.includeStandardAdditions = true;

  if (action === "list_calendars") {
    const calendars = safeCall(calendarApp, "calendars") || [];
    return {calendars: calendars.map(calendarRecord)};
  }

  if (action === "list_events") {
    const startAt = new Date(String(args.start_at || ""));
    const endAt = new Date(String(args.end_at || ""));
    const query = String(args.query || "").toLowerCase();
    const calendarNameFilter = args.calendar_name ? String(args.calendar_name) : null;
    const limit = Number(args.limit || 100);
    const records = allCalendarEvents(calendarApp);
    const filtered = [];

    for (let index = 0; index < records.length && filtered.length < limit; index += 1) {
      const record = records[index];
      if (calendarNameFilter && record.calendarName !== calendarNameFilter) {
        continue;
      }
      const mapped = calendarEventRecord(record.event, record.calendarName);
      const startDate = mapped.start_at ? new Date(mapped.start_at) : null;
      const endDate = mapped.end_at ? new Date(mapped.end_at) : null;
      if (startDate && startDate < startAt) {
        continue;
      }
      if (endDate && endDate > endAt) {
        continue;
      }
      if (query) {
        const haystack = (mapped.title + "\n" + mapped.location + "\n" + mapped.notes).toLowerCase();
        if (haystack.indexOf(query) === -1) {
          continue;
        }
      }
      filtered.push(mapped);
    }

    return {events: filtered};
  }

  if (action === "create_event") {
    const calendar = findCalendar(calendarApp, String(args.calendar_name || ""));
    const event = calendarApp.Event({
      summary: String(args.title || ""),
      startDate: new Date(String(args.start_at || "")),
      endDate: new Date(String(args.end_at || "")),
    });
    if (args.location) {
      event.location = String(args.location);
    }
    if (args.notes) {
      event.description = String(args.notes);
    }
    calendar.events.push(event);
    return calendarEventRecord(event, String(args.calendar_name || ""));
  }

  if (action === "update_event") {
    const eventId = String(args.event_id || "");
    const records = allCalendarEvents(calendarApp);
    for (let index = 0; index < records.length; index += 1) {
      const record = records[index];
      const currentId = String(safeCall(record.event, "id") || "");
      if (currentId !== eventId) {
        continue;
      }
      if (args.title) {
        record.event.summary = String(args.title);
      }
      if (args.start_at) {
        record.event.startDate = new Date(String(args.start_at));
      }
      if (args.end_at) {
        record.event.endDate = new Date(String(args.end_at));
      }
      if (args.location !== null && args.location !== undefined) {
        record.event.location = String(args.location);
      }
      if (args.notes !== null && args.notes !== undefined) {
        record.event.description = String(args.notes);
      }
      return calendarEventRecord(record.event, record.calendarName);
    }
    throw {code: "not_found", message: "No event found for event_id " + eventId};
  }

  throw {code: "validation_error", message: "Unsupported Calendar action: " + action};
}

function runFinder(action, args) {
  if (action === "list_items") {
    return listFinderItems(args);
  }
  if (action === "open_item") {
    return openFinderItem(args);
  }
  if (action === "reveal_item") {
    return revealFinderItem(args);
  }
  if (action === "create_folder") {
    return createFinderFolder(args);
  }
  if (action === "rename_item") {
    return renameFinderItem(args);
  }
  throw {code: "validation_error", message: "Unsupported Finder action: " + action};
}

function remindersListRecord(list) {
  return {
    list_id: String(safeCall(list, "id") || ""),
    name: String(safeCall(list, "name") || ""),
  };
}

function normalizeReminderDue(reminder) {
  const allDayDueDate = safeCall(reminder, "alldayDueDate") || safeCall(reminder, "allDayDueDate");
  if (allDayDueDate) {
    return {due_on: formatDateOnly(allDayDueDate), due_at: null};
  }
  const dueDate = safeCall(reminder, "dueDate");
  return {due_on: null, due_at: toIso(dueDate)};
}

function reminderRecord(reminder, listName) {
  const due = normalizeReminderDue(reminder);
  return {
    reminder_id: String(safeCall(reminder, "id") || ""),
    list_name: listName,
    title: String(safeCall(reminder, "name") || ""),
    notes: String(safeCall(reminder, "body") || ""),
    completed: Boolean(safeCall(reminder, "completed")),
    due_on: due.due_on,
    due_at: due.due_at,
  };
}

function allReminders(app) {
  const lists = safeCall(app, "lists") || [];
  const records = [];
  for (let listIndex = 0; listIndex < lists.length; listIndex += 1) {
    const list = lists[listIndex];
    const listName = String(safeCall(list, "name") || "");
    const reminders = safeCall(list, "reminders") || [];
    for (let reminderIndex = 0; reminderIndex < reminders.length; reminderIndex += 1) {
      records.push({
        list: list,
        listName: listName,
        reminder: reminders[reminderIndex],
      });
    }
  }
  return records;
}

function findRemindersList(app, listName) {
  const lists = safeCall(app, "lists") || [];
  for (let index = 0; index < lists.length; index += 1) {
    if (String(safeCall(lists[index], "name") || "") === listName) {
      return lists[index];
    }
  }
  throw {code: "not_found", message: "No Reminders list named " + listName};
}

function findReminderRecord(app, reminderId) {
  const records = allReminders(app);
  for (let index = 0; index < records.length; index += 1) {
    const record = records[index];
    if (String(safeCall(record.reminder, "id") || "") === reminderId) {
      return record;
    }
  }
  throw {code: "not_found", message: "No reminder found for reminder_id " + reminderId};
}

function applyReminderDue(reminder, args) {
  if (args.clear_due) {
    reminder.alldayDueDate = null;
    reminder.dueDate = null;
    return;
  }
  if (args.due_on) {
    reminder.dueDate = null;
    reminder.alldayDueDate = parseDateOnly(args.due_on);
    return;
  }
  if (args.due_at) {
    reminder.alldayDueDate = null;
    reminder.dueDate = parseIsoDate(args.due_at, "due_at");
  }
}

function runReminders(action, args) {
  const reminders = Application("Reminders");
  reminders.includeStandardAdditions = true;

  if (action === "list_lists") {
    const lists = safeCall(reminders, "lists") || [];
    return {lists: lists.map(remindersListRecord)};
  }

  if (action === "list_reminders") {
    const records = allReminders(reminders);
    const listName = args.list_name ? String(args.list_name) : null;
    const includeCompleted = Boolean(args.include_completed);
    const query = String(args.query || "").toLowerCase();
    const limit = Number(args.limit || 100);
    const filtered = [];
    for (let index = 0; index < records.length && filtered.length < limit; index += 1) {
      const record = records[index];
      if (listName && record.listName !== listName) {
        continue;
      }
      const mapped = reminderRecord(record.reminder, record.listName);
      if (!includeCompleted && mapped.completed) {
        continue;
      }
      if (query) {
        const haystack = (mapped.title + "\n" + mapped.notes).toLowerCase();
        if (haystack.indexOf(query) === -1) {
          continue;
        }
      }
      filtered.push(mapped);
    }
    return {reminders: filtered};
  }

  if (action === "create_reminder") {
    const list = findRemindersList(reminders, String(args.list_name || ""));
    const reminder = reminders.Reminder({
      name: String(args.title || ""),
      body: args.notes ? String(args.notes) : "",
    });
    applyReminderDue(reminder, args);
    list.reminders.push(reminder);
    return reminderRecord(reminder, String(args.list_name || ""));
  }

  if (action === "update_reminder") {
    const record = findReminderRecord(reminders, String(args.reminder_id || ""));
    if (args.title) {
      record.reminder.name = String(args.title);
    }
    if (args.notes !== null && args.notes !== undefined) {
      record.reminder.body = String(args.notes);
    }
    applyReminderDue(record.reminder, args);
    return reminderRecord(record.reminder, record.listName);
  }

  if (action === "complete_reminder") {
    const record = findReminderRecord(reminders, String(args.reminder_id || ""));
    record.reminder.completed = true;
    return reminderRecord(record.reminder, record.listName);
  }

  throw {code: "validation_error", message: "Unsupported Reminders action: " + action};
}

function chatParticipants(chat) {
  return safeCall(chat, "participants") || [];
}

function chatDisplayName(chat) {
  const chatName = safeCall(chat, "name");
  if (chatName) {
    return String(chatName);
  }
  const participants = chatParticipants(chat);
  const parts = [];
  for (let index = 0; index < participants.length; index += 1) {
    const participant = participants[index];
    const label = safeCall(participant, "fullName")
      || safeCall(participant, "name")
      || safeCall(participant, "handle")
      || safeCall(participant, "id");
    if (label) {
      parts.push(String(label));
    }
  }
  return parts.join(", ");
}

function chatRecord(chat) {
  const account = safeCall(chat, "account");
  const participants = chatParticipants(chat);
  return {
    chat_id: String(safeCall(chat, "id") || ""),
    name: chatDisplayName(chat),
    participant_count: participants.length,
    service: String((account && safeCall(account, "serviceType")) || ""),
    account_id: String((account && safeCall(account, "id")) || ""),
  };
}

function findChat(app, chatId) {
  const chats = safeCall(app, "chats") || [];
  for (let index = 0; index < chats.length; index += 1) {
    if (String(safeCall(chats[index], "id") || "") === chatId) {
      return chats[index];
    }
  }
  throw {code: "not_found", message: "No chat found for chat_id " + chatId};
}

function normalizeFromMe(value) {
  if (typeof value === "boolean") {
    return value;
  }
  const text = String(value || "").toLowerCase();
  if (text === "outgoing" || text === "sent" || text === "fromme" || text === "true") {
    return true;
  }
  if (text === "incoming" || text === "received" || text === "false") {
    return false;
  }
  return null;
}

function messageRecord(message) {
  const fromMe = safeCall(message, "fromMe");
  const isFromMe = safeCall(message, "isFromMe");
  const direction = safeCall(message, "direction");
  return {
    text: String(
      safeCall(message, "text")
      || safeCall(message, "content")
      || safeCall(message, "body")
      || ""
    ),
    sent_at: toIso(
      safeCall(message, "dateSent")
      || safeCall(message, "timeSent")
      || safeCall(message, "dateReceived")
      || safeCall(message, "sentAt")
      || safeCall(message, "time")
    ),
    from_me: normalizeFromMe(fromMe !== null ? fromMe : (isFromMe !== null ? isFromMe : direction)),
  };
}

function readMessageHistory(chat, limit) {
  const readers = ["messages", "texts"];
  for (let index = 0; index < readers.length; index += 1) {
    const methodName = readers[index];
    if (typeof chat[methodName] !== "function") {
      continue;
    }
    try {
      const rawMessages = chat[methodName]();
      const messages = rawMessages && rawMessages.length !== undefined
        ? Array.prototype.slice.call(rawMessages)
        : [];
      const recent = messages.slice(Math.max(0, messages.length - limit)).map(messageRecord);
      return recent;
    } catch (error) {
      continue;
    }
  }
  throw {
    code: "unsupported_history",
    message: (
      "The selected Messages chat does not expose readable message history through macOS scripting."
    ),
  };
}

function runMessages(action, args) {
  const messages = Application("Messages");
  messages.includeStandardAdditions = true;

  if (action === "list_chats") {
    const chats = safeCall(messages, "chats") || [];
    const limit = Number(args.limit || 100);
    return {chats: chats.slice(0, limit).map(chatRecord)};
  }

  if (action === "read_recent_messages") {
    const chatId = String(args.chat_id || "");
    const limit = Number(args.limit || 20);
    const chat = findChat(messages, chatId);
    return {chat_id: chatId, messages: readMessageHistory(chat, limit)};
  }

  throw {code: "validation_error", message: "Unsupported Messages action: " + action};
}

function normalizeError(error) {
  const message = String((error && error.message) || error || "Unknown macOS helper error");
  const number = error && error.errorNumber !== undefined ? String(error.errorNumber) : "";
  const code = String((error && error.code) || "");

  if (code === "permission_denied" || number === "-1743" || message.indexOf("-1743") !== -1) {
    return err("permission_denied", "macOS Automation permission denied");
  }
  if (code) {
    return err(code, message);
  }
  return err("script_error", message);
}

function dispatch(payload) {
  const app = String(payload.app || "");
  const action = String(payload.action || "");
  const args = payload.args || {};

  if (app === "finder") {
    return ok(runFinder(action, args));
  }
  if (app === "calendar") {
    return ok(runCalendar(action, args));
  }
  if (app === "notes") {
    return ok(runNotes(action, args));
  }
  if (app === "reminders") {
    return ok(runReminders(action, args));
  }
  if (app === "messages") {
    return ok(runMessages(action, args));
  }

  return err("validation_error", "Unsupported macOS app: " + app);
}

function run(argv) {
  try {
    return JSON.stringify(dispatch(readInput()));
  } catch (error) {
    return JSON.stringify(normalizeError(error));
  }
}
