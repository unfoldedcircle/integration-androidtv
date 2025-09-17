"""Application link and identifier mappings."""


def is_homescreen_app(app_id: str) -> bool:
    """Check if the app is a homescreen app."""
    # make sure each app_id is defined in IdMappings below!
    return app_id in [
        "com.android.systemui",
        "com.google.android.tvlauncher",
        "com.google.android.apps.tv.launcherx",
        "com.spocky.projengmenu",  # Projectivity Launcher
    ]


def is_standby_app(app_id: str) -> bool:
    """Check if the app is a standby app."""
    # make sure each app_id is defined in IdMappings below!
    return app_id in ["com.google.android.backdrop", "com.google.android.apps.tv.dreamx"]


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
    "Kodi": {"url": "market://launch?id=org.xbmc.kodi"},
    "1und1": {"url": "1und1tv://1und1.tv"},
    "Arte": {"url": "arte://display"},
    "Google Play Store": {"url": "https://play.google.com/store/"},
    "DVB-C/T/S": {"url": "market://launch?id=org.droidtv.playtv"},
    "ATV Inputs": {"url": "market://launch?id=org.droidtv.channels"},
    "ATV PlayFI": {"url": "market://launch?id=com.phorus.playfi.tv"},
    "ATV Now on TV": {"url": "market://launch?id=org.droidtv.nettvrecommender"},
    "ATV Media": {"url": "market://launch?id=org.droidtv.contentexplorer"},
    "ATV Browser": {"url": "market://launch?id=com.vewd.core.browserui"},
    "Quickline TV": {"url": "market://launch?id=ch.quickline.tv.uhd"},
    "myCANAL": {"url": "market://launch?id=com.canal.android.canal"},
}

# Direct application-id mappings to friendly names
# Used to show which app is currently in the foreground (currently playing)
IdMappings = {
    "com.google.android.backdrop": "Backdrop Daydream",
    "com.google.android.apps.tv.dreamx": "Backdrop Daydream",
    "com.google.android.katniss": "Google app",
    "com.android.systemui": "Android TV",
    "com.google.android.tvlauncher": "Android TV",
    "com.google.android.apps.tv.launcherx": "Android TV",
    "com.google.android.gms": "Google Play services",
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
    "com.nathnetwork.supersmart": "SuperSmart",
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
    "org.droidtv.playtv": "DVB-C/T/S",
    "ch.quickline.tv.uhd": "Quickline TV",
}

# Application-ID substring mappings to friendly names
# Used to show which app is currently in the foreground (currently playing)
NameMatching = {
    "youtube": "YouTube",
    "videomanager": "YouTube",
    "amazonvideo": "Prime Video",
    "apple": "Apple TV",
    "plex": "Plex",
    "kodi": "Kodi",
    "emby": "Emby",
    "dune": "Dune HD",
    "einsundeins": "1und1 TV",
}
