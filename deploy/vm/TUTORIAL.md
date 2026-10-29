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

It asks a few questions first. For most practices, the defaults are right:
press Enter for a new project and an automatic web address, and answer "n"
to Google sign-in and transcription. You can add both later.

Then it shows what it will set up and what it costs, and asks you to
confirm. The install takes about 15 minutes.

## Sign in

When it finishes, it prints your Pablo address. Open it and choose
**Create account** with the email you gave the installer. You will verify
your email and set up an authenticator app, and then you are in.

If the installer stops partway, it prints the command that picks up where it
left off.

<walkthrough-conclusion-trophy></walkthrough-conclusion-trophy>

Updates, backups and day-to-day commands are in
[deploy/vm/README.md](https://github.com/pablo-health/pablo/blob/main/deploy/vm/README.md).
