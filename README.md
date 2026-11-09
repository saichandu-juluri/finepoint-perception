# FinePoint Perception

Send a recording session from your Mac or Windows PC to FinePoint Cloud, and watch all its cameras side by side.

## Watch how it works (1.5 minutes)


https://github.com/user-attachments/assets/fc08ee6a-7357-4ec4-957a-66a737660c9b


The video in full quality, and each step as its own short video: [videos](https://github.com/saichandu-juluri/finepoint-perception/releases/tag/video-2026-11-09).

## What you need

- A Mac with an Apple chip (M1 or newer) and macOS 14 or newer, or a PC with Windows 10 or 11, 64-bit (x64)
- About 3 GB of free disk space
- From the FinePoint team: the server address, your email and a password. The team's email has a link where you set the password.

## Install on a Mac

1. Open Terminal: in Finder, open **Applications**, then **Utilities**, then double-click **Terminal**.
2. Paste this line into Terminal and press Return:

   ```sh
   git clone https://github.com/saichandu-juluri/finepoint-perception.git ~/finepoint-perception && ~/finepoint-perception/install.command
   ```

   If the Mac asks to install the command line developer tools, click **Install**. When that is done, paste the line again.
   Warnings are normal. The first time takes a few minutes. Then the app opens in your browser.
3. From then on, click **FinePoint Perception** in Launchpad (Apps on newer Macs), or in the Applications folder of your
   home folder. The app opens in your browser.

## Install on Windows

1. Open PowerShell: click **Start**, type **PowerShell**, and click **Windows PowerShell**.
2. Paste this line into PowerShell (right-click, or press Ctrl+V) and press Enter:

   ```powershell
   irm -useb https://raw.githubusercontent.com/saichandu-juluri/finepoint-perception/main/install.ps1 | iex
   ```

   The first time takes a few minutes. Then the app opens in your browser.
3. From then on, click **FinePoint Perception** in the Start menu or on the desktop. The app opens in your browser.

## Upload a session

1. Click **New project**.
2. Go to the recording folder, the one with one folder per camera inside. Click a folder name to open it, or **Up** to
   go back. On Windows, click a drive, such as **E:** for a USB drive or a memory card, to open it. If the Mac asks
   whether the app may open a folder or a drive, click **Allow**.
3. Check the camera names. Leave the board numbers as they are.
4. Type a session name and click **Create the project**. The session's page opens.
5. The first time only: under **Upload to the cloud**, type the server address, your email and your password, and click
   **Sign in**.
6. Click **Upload**.

The bar shows how far the upload is and the time left. It is done when the page says
**Uploaded. Everything is in the cloud.**

You can close the browser at any time. **Pause** stops the upload, and **Resume** carries on where it stopped.
After a restart of the computer, click the app's icon and the upload carries on.

## Line up the cameras (optional)

The upload does not need this. On the session's page, click **Run** and wait until it ends. Then click
**Open multi-view**.

A video the app could not line up, for example one without sound, is listed under **To place by hand**. Move it until
it matches the others, click **Save**, then click **Rerun the clock**.

## If something goes wrong

- The page asks you to sign in again: sign in again under **Upload to the cloud**. If you see **Sign out**, click it
  first.
- Anything else: send the FinePoint team a screenshot.

## Update on a Mac

Paste this line into Terminal and press Return:

```sh
git -C ~/finepoint-perception pull && ~/finepoint-perception/install.command
```

## Update on Windows

Paste the install line into PowerShell again and press Enter:

```powershell
irm -useb https://raw.githubusercontent.com/saichandu-juluri/finepoint-perception/main/install.ps1 | iex
```

It stops the app if it runs, updates it, and opens it again. An upload carries on.

For developers: see [docs/DEVELOP.md](docs/DEVELOP.md).
