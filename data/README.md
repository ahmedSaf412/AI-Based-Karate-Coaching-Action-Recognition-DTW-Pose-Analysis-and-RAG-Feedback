# Data (not in git, shared privately)
Expected layout (mounted read-only at /data in the container):

    karate_normalized_base.csv          landmark CSV used for CV / golden split
    Examplers/                          18 reference clips  <Move>_<stance>.mp4
    Karate_phase/segmentedTest/         11 Academy clips    e.g. MaeGeri_side_048_0002.mp4
    Karate_phase/Distractors/           7 non-karate clips

Filmed practitioners and web videos have consent/licence limits: do not publish the videos.
Create MANIFEST.sha256 with:  cd data && sha256sum $(find . -type f ! -name 'MANIFEST.sha256' ! -name 'README.md' ! -name '.gitkeep') > MANIFEST.sha256
Verify with:                  cd data && sha256sum -c MANIFEST.sha256
