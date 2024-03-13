"""Application link and identifier mappings."""

# Application launch links
Apps = {
    "Youtube": {"url": "https://www.youtube.com"},
    "Prime Video": {"url": "https://app.primevideo.com"},
    "Plex": {"url": "plex://"},
    "Netflix": {"url": "netflix://"},
    "HBO Max": {"url": "https://play.hbomax.com"},
    "Max": {"url": "https://play.max.com"},
    "Emby": {"url": "embyatv://tv.emby.embyatv/startapp"},
    "Disney+": {"url": "https://www.disneyplus.com"},
    "Apple TV": {"url": "https://tv.apple.com"},
    "Spotify": {"url": "spotify://"},
    "Ziggo": {"url": "ziggogo://"},
    "Videoland": {"url": "videoland-v2://"},
    "Steam Link": {"url": "steamlink://"},
    "Waipu TV": {"url": "waipu://tv"},
    "Magenta TV": {"url": "atv://de.telekom.magentatv"},
    "Zattoo": {"url": "zattoo://zattoo.com"},
    "Pluto TV": {"url": "https://pluto.tv/"},
    "ARD Mediathek": {"url": "https://www.ardmediathek.de/"},
    "ZDF Mediathek": {"url": "https://www.zdf.de/filme"},
    "Kodi": {"url": "https://play.google.com/store/apps/details?id=org.xbmc.kodi"}
}

# Direct application-id mappings to friendly names
# Used to show which app is currently in the foreground (currently playing)
IdMappings = {
    "com.google.android.backdrop": "Backdrop Daydream",
    "com.google.android.apps.tv.dreamx": "Backdrop Daydream",
    "com.google.android.katniss": "Google app",
    "com.google.android.apps.tv.launcherx": "Android TV",
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
    "com.google.android.videos": "Google TV",
    "tv.wuaki": "Rakuten TV",
    "homedia.sky.sport": "SKY",
    "com.teamsmart.videomanager.tv": "SmartTube",
    "nl.rtl.videoland.v2": "Videoland",
    "com.disney.disneyplus": "Disney+",
    "com.netflix.ninja": "Netflix",
    "org.jellyfin.androidtv": "Jellyfin",
    "com.discovery.dplay": "discovery+",
    "com.talpa.kijk": "KIJK",
    "nl.newfaithnetwork.app": "New Faith Network",
    "nl.uitzendinggemist": "NPO Start",
    "com.valvesoftware.steamlink": "Steam Link",
    "org.videolan.vlc": "VLC",
    "com.ziggo.tv": "Ziggo GO TV",
    "com.hbo.hbonow": "HBO Max",
    "com.wbd.stream": "Max",
    "de.swr.avp.ard.tv": "ARD Mediathek",
    "com.zdf.android.mediathek": "ZDF Mediathek",
    "de.exaring.waipu": "Waipu TV",
    "de.telekom.magentatv.tv": "Magenta TV",
    "tv.pluto.android": "Pluto TV",
    "com.nvidia.ota": "System upgrade",
}

# Application-ID substring mappings to friendly names
# Used to show which app is currently in the foreground (currently playing)
NameMatching = {
    "youtube": "YouTube",
    "amazonvideo": "Prime Video",
    "apple": "Apple TV",
    "plex": "Plex",
    "kodi": "Kodi",
    "emby": "Emby",
}
