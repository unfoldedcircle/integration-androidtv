"""Application link and identifier mappings."""
# Application launch links
Apps = {
    "Youtube": {"url": "https://www.youtube.com"},
    "Prime Video": {"url": "https://app.primevideo.com"},
    "Plex": {"url": "plex://"},
    "Netflix": {"url": "netflix://"},
    "HBO Max": {"url": "https://play.hbomax.com"},
    "Emby": {"url": "embyatv://tv.emby.embyatv/startapp"},
    "Disney+": {"url": "https://www.disneyplus.com"},
    "Apple TV": {"url": "https://tv.apple.com"},
}

# Direct application-id mappings to friendly names
# Used to show which app is currently in the foreground (currently playing)
IdMappings = {
    "com.google.android.backdrop": "Backdrop Daydream",
    "com.google.android.katniss": "Google app",
    "com.android.vending": "Google Play Store",
    "com.android.tv.settings": "Settings",
    "com.spotify.tv.android": "Spotify",
    "com.cbs.ca": "Paramount+",
    "com.apple.android.music": "Apple Music",
    "com.apple.atve.androidtv.appletv": "Apple TV",
    "net.init7.tv": "TV7",
    "com.zattoo.player": "Zattoo",
    "com.swisscom.tv2": "Swisscom blue TV",
    "ch.srgssr.playsuisse.tv": "Play Suisse",
    "ch.srf.mobile.srfplayer": "Play SRF",
    "com.nousguide.android.rbtv": "Red Bull TV",
    "tv.arte.plus7": "ARTE",
    "com.zdf.android.mediathek": "ZDFmediathek",
    "com.google.android.videos": "Google TV",
    "tv.wuaki": "Rakuten TV",
    "homedia.sky.sport": "SKY",
}

# Application-ID substring mappings to friendly names
# Used to show which app is currently in the foreground (currently playing)
NameMatching = {
    "netflix": "Netflix",
    "youtube": "YouTube",
    "amazonvideo": "Prime Video",
    "hbomax": "HBO Max",
    "disney": "Disney+",
    "apple": "Apple TV",
    "plex": "Plex",
    "kodi": "Kodi",
    "emby": "Emby",
}
