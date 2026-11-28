Resident Access 0
A screen reader mod for Resident Evil 0 HD Remaster (Steam), so the game can be
played blind with an Xbox controller. It speaks through your screen reader
(NVDA, JAWS, ZoomText, System Access and others), or Windows' own voice if no
screen reader is running.

This is an early build (0.42). It is built from Resident Access HD
(the mod for Resident Evil HD Remaster), and more is coming.

Installing
1. Unzip this folder anywhere you like (Documents is fine).
2. Run "Install Resident Access 0.bat". It finds the game through Steam and
   puts a small controller add-on (xinput1_3.dll) in the game's folder.
   If the game is open, close it and start it again afterwards.
3. Start Resident Evil 0 HD Remaster from Steam. The mod starts with it in a
   small window and says "Resident Access 0 ready" once it has found the game.
Python comes with the mod, so nothing else needs installing.
If the mod ever isn't running, "Start Resident Access 0.bat" starts it
yourself (starting it again closes the old copy).
To take it out again, run "Uninstall Resident Access 0.bat".

Updates
The mod checks for a new version each time it starts, and updates itself.
"Update Resident Access 0.bat" checks straight away. Your settings
(settings.json) and the log are kept.

Before you play
When you start a new game, set Control Type to Original in Game Setup (or in
Options, Controls). Up then always walks the way you face, and left and right
turn you on the spot: auto walk and the 3D beacon need this. Keep Subtitles On
so cutscenes are read (with Subtitles Off the mod does not read them).

What it reads by itself
- The title screen, main menu, Load Game and its save slots, the difficulty
  list and Game Setup, as you move through them.
- Every message in play: what you find when you check things, locked doors,
  questions and their Yes and No, the pause menu, saving and loading, and
  cutscene subtitles.
- Your item screen (X): each item as you move to it, with how many
  ("Handgun, 15 rounds", "Handgun Ammo, 30"), "equipped" after the weapon
  in your hand, and "Empty" on an empty slot. The game shows "Equip weapon"
  for both choices: the mod says "Un-equip weapon" when it will take the
  weapon out of your hand, and says what you have equipped when it changes.
- With your partner: Y on the item screen shows the other one's items (and
  makes them the one you play). The mod says whose items they are
  ("Billy's items. Handgun, 15 rounds"), with each one's own amounts.
- Changing items with your partner: picking one of their slots says whose
  they are and each slot's item or "Empty"; for ammo and the like, the
  number to pass over is read ("15 of 15") as you change it.
- Files: on the item screen's Files page each file is named as you move
  ("Player's Manual 1, 1 of 3"), and the whole file is read when you open
  it. A file you pick up is read page by page as you turn the pages.
- The typewriter: its question, then each save slot as you move to it.
- Load Game: Continue, Restart and Back after you pick a slot.
- The train brakes' number pads: the number to make, the total so far, and
  the number the cursor is on as you move (it wraps round), on both pads.
- Examining an item (turning it round on the item screen): which side faces
  you as you turn it ("Front cover", "Spine", "Page edges, at an angle" for
  a book; front, back, left side, right side, top and bottom for others).
- The clock: the time it shows when you are asked which hand to turn, and
  the new time each time a hand moves ("8:15"). Its buttons (Left, Right,
  Hour hand or Minute hand, OK) are said only as you move onto them.
- The combination dials (the Duralumin Case): when they come up, the numbers
  and which dial you are on; then the dial's name and number as you move
  (left, middle, right), and each new number as you turn it.
- The combination lock on the door to Machine Room 1F (once the Dial is fitted):
  the number the dial shows each time you turn it, and again after the game
  asks for the next digit. The code is in the Leech Growth Records.
- Pictures that carry a clue are described after their words: the Microfilm
  Image (and the projector showing both microfilms) says which number is on
  which seat of the conference room.
- Levers: "Holding" when you pull one and keep hold of it (the hookshot
  release), and "Let go" when the one holding it moves away.
- The room's name as you go in (the train's cars by their names on the
  game's map; other areas by area and number for now).
- When Rebecca or Billy dies, and who you are playing as when you switch.
- The room's name is said once: not again when the game shows a short scene
  from another room (pulling the hookshot release).

The list (the e list)
Everything in the room you are in, in six lists: Doors, Items, Things to
check, Puzzles, Enemies and Partner. Each thing is said with how far it is and which
way ("Door to Dining Car, 7 metres, to the right"). Things on another floor
of the room say "upstairs" or "downstairs".
- Doors say where they lead. Stairs and ladders are listed as stairs and
  ladders ("Ladder up to Service Room", "Stairs down to Dining Car").
- Places to jump down, climb up or step up ("Jump down", "Climb up", "Step
  up") are listed too; auto walk uses them by itself.
- Items are named before you pick them up ("Handgun Ammo, 15"), and leave the
  list once taken. Key items (Briefcase, Train Key, Hookshot...) are listed
  too, and an item you take from something (the hookshot fixed to the wall)
  is listed at that thing. Items you drop are listed where you left them.
- Things to check are named from what the game says about them, and
  include the typewriter.
- Puzzles: the things that ask you something when you check them -
  reconnect the cable? press the button? place an item here? - places
  where an item is used (the window for the hookshot, the microfilm
  projector), panels that open a close-up (the brakes on the train), lifts
  you stand a character on, and things you push:
  - The table in the Assistant Director's Office and the bookcase in the
    Small Library are listed with what to do next and how many pushes are
    left ("push it along the bookshelves to the wall at the end, 7 more
    pushes"). Auto walk takes you to the side to push from, facing the right
    way, and says "Hold forward to push". As you push it counts down
    ("6 more"), and says "Stop" (holding the controller back a moment) once
    it is in place. If it goes the wrong way, leave the room and come back
    to put it back where it started.
  - The table is climbed over for you when it is in the way, and with it
    under the moose head, auto walk to the Iron Needle (or the moose head)
    climbs onto the table and faces it. Standing on the table, auto walk to
    anything else gets you down first.
  - The crate that comes down on the lift outside the Training Facility
    entrance (once the Battery powers it): while it is in the lift, auto walk
    takes you to its front to climb over it (A, walk to the far edge, A);
    then out of the lift, west, south and against the pillar, with the
    pushes counted down. Auto walk to the Discipline Tablet climbs onto it
    and faces the tablet. Your partner standing in the way stops the crate:
    the right bumper tells them to wait (and to follow again).
  - The white king in the chess room, pushed south, east and north; any
    other piece lets out gas.
  - A place a key item was taken from stays listed while someone carries
    it, as where it goes back ("Where the Battery goes").
  - The lecture room's lectern, and its sixteen seats ("Seat 2, front row,
    right of the aisle"), numbered as on the Microfilm Image: auto walk
    takes you behind the seat's desk, facing it.
  - Places to use the Hookshot (the hole the bat makes in the Church roof):
    auto walk takes you under it and says to use the Hookshot from your
    items.
- The lists change as you go into each new room.
- Enemies: what each one is, how far, and how much health it has left, in
  percent. As you shoot one its health is said, and "dead" when it dies
  ("Zombie dead"). Dead ones, and bodies left from before, are not listed.
- Partner: Rebecca or Billy, whoever you are not playing, with their health,
  or "in another room".
- With Rebecca and Billy in different rooms, everything is still measured
  from the room you are in, and only that room's enemies are listed.

The controller list
Hold the left bumper (without aiming) and, while you hold it:
- D-pad right or left: the next or previous list. Pressing the bumper says
  which list you are in.
- D-pad down and up: the next and previous thing in it, nearest first.
- Press the left stick in: auto walk takes you there.
- Press the right stick in: the 3D beacon to it, to walk there yourself.
  Again to stop it.
- B: stop walking.
- Y: the way to it again.
- X: your health.
- A: the room, how many things it has, and its doors.
- Right bumper: the enemies in the room.
While you hold the left bumper, these buttons do nothing in the game, so you
will not do anything by accident.
Without the bumper, press the right stick in at any time in play to hear your
health (both characters' when Billy is with you).

Aim beep
While you hold the aim button (right bumper) a soft bell rings when firing now
would hit a living enemy: one lined up in front of you, in range, with no wall
in between. Bats up in the air count when they are right above you.

Auto walk
It finds the way round walls and furniture and stops facing the thing, close
enough to use it, and says "Here. Press A". Just press A to open the door,
pick up the item or check the thing. If it could not quite get you there it
just says "Here." A beep plays
while it walks you, the same as in Resident Access HD. It walks; hold the run
button (B) while it walks you and it runs (it still walks the last steps and
the sharp bends). Touch the controller, or press the
left stick in again, to stop it. If there is no way there it tells you (and when the thing is
upstairs or downstairs from you).
If the way goes over an edge or a step (the sewer's water channel, the Small
Library's platform), auto walk takes you to the game's own spot for it,
faces across and presses A for you, then walks on.
Sometimes you may have to turn left or right a little, or step forward a
little, before A works on something: an item, a door, stairs or a thing to
check. If "Here. Press A" does nothing, turn a touch each way, or take a
small step forward, and press A again.
Some things to check only do something later in the story (Edward's body on
the train, for one): pressing A there may do nothing yet.

The 3D beacon
Walk there yourself, following a sound all round you. Use headphones.
Pick something in the list, hold the left bumper and press the right stick
in. A knock plays where the next turn of the way is: in front, behind or to
either side. Turn until it is straight ahead and walk. It knocks faster as
you get near, and a chime plays at each turn. At the end it says Here.
The 3D sound is OpenAL Soft (lib\openal_soft.dll, LGPL licence in
lib\OpenAL-COPYING.txt).

Mod settings
In play, press Start for the game's menu: Back, Quit Game, then "Mod
settings" (the rows go round, so one step up from Back gets there too). The
"Quit the game?" question under Quit Game is read, with Yes or No as you move. Press A to open it. Up and down choose, left
and right change, B goes back.
- 3D beacon, Auto walk beep, Arrival chime and Aim beep volumes: 0 to 200
  percent.
- Mod sounds: on or off. Off silences every sound the mod makes; speech
  carries on.

Speech
With NVDA running, the mod speaks through NVDA. Otherwise it uses Prism
(lib\prism.dll, MPL 2.0 licence in lib\prism-LICENSE.txt) to speak through
JAWS, ZoomText, System Access, PC-Talker, ZDSR or another screen reader that is
running, and with none of them, through Windows' own voices.

If something goes wrong
This mod is for Resident Evil 0 (its game file is re0hd.exe). If something
says bhd.exe is missing, that is Resident Access HD, the mod for Resident
Evil 1: download Resident Access 0 from
https://github.com/RetroSanity/Resident-Access-0/releases instead.
Everything the mod says, and any errors, are written to re0access.log in
this folder. It starts fresh each time the mod starts; the time before is
kept as re0access.old.log. If you report a problem, please include both
files and say where in the game you were.
