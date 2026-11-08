# Security policy

## Supported versions

pokus-platform is pre-release: no version has been released yet, and
fixes go to the default branch only.

## Reporting a vulnerability

Report it privately with GitHub's private vulnerability reporting: on the
repository's **Security** tab, choose **Report a vulnerability**. Do not open
a public issue, pull request or discussion for it.

Include what is affected (the component or file), how to reproduce it and
what an attacker gains. Leave out data from a real lab: host names,
addresses, account names, keys and logs that name them. The maintainer
answers in the private advisory, and the fix is published with a GitHub
security advisory.

## Scope

In scope: everything in this repository, among it the services, their
socket and privilege design in the platform manifest, and the CI checks.

Out of scope: a vulnerability in pokus-infrastructure, which provisions the
lab host (report it there), or in an upstream component the platform uses:
report it to that project.
