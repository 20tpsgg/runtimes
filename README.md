# Runtimes

Image: `ghcr.io/20tpsgg/runtimes`

```text
java_8   java_9   java_10  java_11  java_12
java_13  java_14  java_15  java_16  java_17
java_18  java_19  java_20  java_21  java_22
java_23  java_24  java_25  java_26  java_27
```

LTS: 8, 11, 17, 21, 25. Current non-LTS: 27.
Other versions are EOL and no longer receive JDK security updates.

Platforms: amd64 and arm64. Java 9, 10, 12 and 14 are amd64-only.

Actions builds, tests and publishes on runtime changes to `master` or manually.
Daily checks skip unchanged images. Set the GHCR package to **Public** after first publication.
