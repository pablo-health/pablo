# Compose supplies build contexts and Dockerfiles. This overlay gives each
# image its Compose tag and an independent GitHub Actions cache scope so one
# image cannot evict another image's cache entry.

group "default" {
  targets = [
    "backend",
    "frontend",
    "firebase-auth",
    "fake-clearinghouse",
    "fake-dns",
    "fake-gcs",
    "fake-google",
    "fake-ical",
    "fake-llm",
    "fake-mail",
    "fake-nppes",
    "fake-sms",
  ]
}

target "backend" {
  tags = [
    "pablo-e2e-backend",
    "pablo-e2e-migrate",
    "pablo-e2e-seed-second-practice",
  ]
  cache-from = ["type=gha,scope=e2e-backend"]
  cache-to = ["type=gha,mode=max,scope=e2e-backend"]
}

target "frontend" {
  tags = ["pablo-e2e-frontend"]
  cache-from = ["type=gha,scope=e2e-frontend"]
  cache-to = ["type=gha,mode=max,scope=e2e-frontend"]
}

target "firebase-auth" {
  tags = ["pablo-e2e-firebase-auth"]
  cache-from = ["type=gha,scope=e2e-firebase-auth"]
  cache-to = ["type=gha,mode=max,scope=e2e-firebase-auth"]
}

target "fake-clearinghouse" {
  tags = ["pablo-e2e-fake-clearinghouse"]
  cache-from = ["type=gha,scope=e2e-fake-clearinghouse"]
  cache-to = ["type=gha,mode=max,scope=e2e-fake-clearinghouse"]
}

target "fake-dns" {
  tags = ["pablo-e2e-fake-dns"]
  cache-from = ["type=gha,scope=e2e-fake-dns"]
  cache-to = ["type=gha,mode=max,scope=e2e-fake-dns"]
}

target "fake-gcs" {
  tags = ["pablo-e2e-fake-gcs", "pablo-e2e-fake-gcs-key"]
  cache-from = ["type=gha,scope=e2e-fake-gcs"]
  cache-to = ["type=gha,mode=max,scope=e2e-fake-gcs"]
}

target "fake-google" {
  tags = ["pablo-e2e-fake-google"]
  cache-from = ["type=gha,scope=e2e-fake-google"]
  cache-to = ["type=gha,mode=max,scope=e2e-fake-google"]
}

target "fake-ical" {
  tags = ["pablo-e2e-fake-ical"]
  cache-from = ["type=gha,scope=e2e-fake-ical"]
  cache-to = ["type=gha,mode=max,scope=e2e-fake-ical"]
}

target "fake-llm" {
  tags = ["pablo-e2e-fake-llm"]
  cache-from = ["type=gha,scope=e2e-fake-llm"]
  cache-to = ["type=gha,mode=max,scope=e2e-fake-llm"]
}

target "fake-mail" {
  tags = ["pablo-e2e-fake-mail"]
  cache-from = ["type=gha,scope=e2e-fake-mail"]
  cache-to = ["type=gha,mode=max,scope=e2e-fake-mail"]
}

target "fake-nppes" {
  tags = ["pablo-e2e-fake-nppes"]
  cache-from = ["type=gha,scope=e2e-fake-nppes"]
  cache-to = ["type=gha,mode=max,scope=e2e-fake-nppes"]
}

target "fake-sms" {
  tags = ["pablo-e2e-fake-sms"]
  cache-from = ["type=gha,scope=e2e-fake-sms"]
  cache-to = ["type=gha,mode=max,scope=e2e-fake-sms"]
}
