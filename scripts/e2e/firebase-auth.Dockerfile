# Firebase Auth emulator for the end-to-end stack (docker-compose.e2e.yml).
#
# Only the auth emulator runs, which is pure Node: no JVM, unlike the
# Firestore and database emulators. The firebase-tools version is pinned so
# the emulator's token shapes don't drift under the suite.
FROM node:24-slim

RUN npm install -g firebase-tools@15.29.0

# The emulator writes its debug logs into the working directory, so the
# unprivileged user the image ships with must own it.
WORKDIR /srv
COPY --chown=node:node firebase.json ./
RUN chown node:node /srv

USER node

EXPOSE 9099

# A "demo-" project id needs no credentials and never reaches Google.
CMD ["firebase", "emulators:start", "--only", "auth", "--project", "demo-pablo-e2e"]
