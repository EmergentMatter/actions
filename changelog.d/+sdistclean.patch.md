The shared scripts checkout is moved out of the consumer workspace before anything builds, so a release sdist no longer contains an `.em-actions/` directory, and the build fails if one ever does.
