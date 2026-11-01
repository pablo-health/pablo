# Install Pablo on your own Google Cloud

<walkthrough-tutorial-duration duration="20"></walkthrough-tutorial-duration>

Pablo runs on one small server in your own Google Cloud account. Your
records stay there. It costs about $20 a month, billed by Google.

## Before you start

You need two things in place:

1. **A billing account.** If you have never used Google Cloud,
   [add one here](https://console.cloud.google.com/billing/create).
2. **Google's Business Associate Agreement**, accepted by an administrator of
   your account. It is free and takes a few minutes:
   [how to accept it](https://cloud.google.com/security/compliance/hipaa).

## Run the installer

Click the copy button to send this to the terminal below, then press Enter:

```bash
./install.sh
```

It asks a few questions first:

- **Project:** press Enter for a new one.
- **Address:** we recommend **2**, a name you choose, such as
  yourpractice.pablo.clinic. Choose 1 for your own domain.
- **Google sign-in and transcription:** answer "n" for now. You can add both
  later.

Then it shows what it will set up and what it costs, and asks you to
confirm. The install takes about 15 minutes.

## Save your backup passphrase

At the end, the installer shows your backup passphrase once. Restoring your
backups needs it. Copy it into a password manager, or print it, before you
close this window.

## Sign in

Open the address it printed and choose **Create account** with the email you
gave the installer. Verify your email, then add a passkey or an authenticator
app.

If the installer stops partway, it prints the command that picks up where it
left off.

<walkthrough-conclusion-trophy></walkthrough-conclusion-trophy>

Next, the desktop app that records your sessions, and everything after the
install:
[Start here](https://github.com/pablo-health/pablo/blob/main/docs/self-hosting/start-here.md).
