# 白饭下载器 —— aria2 配置模板
# 本文件由程序读取后填充占位符，生成的 conf/aria2.conf 请勿手工编辑；
# 想改参数请用「选项」界面，改动会写回 data/settings.json 后重新生成。

dir=@DOWNLOAD_DIR@
continue=true
disk-cache=64M
file-allocation=none

max-connection-per-server=@MAX_CONN@
split=@SPLIT@
min-split-size=1M
max-concurrent-downloads=@MAX_CONC@
max-tries=5
retry-wait=5
timeout=60
connect-timeout=60
max-overall-download-limit=@MAX_DL@
max-overall-upload-limit=@MAX_UL@
max-download-limit=@PER_DL@
max-upload-limit=@PER_UL@

log=@LOG_FILE@
log-level=notice
console-log-level=warn
summary-interval=30

enable-dht=@ENABLE_DHT@
enable-dht6=@ENABLE_DHT6@
dht-listen-port=6881-6999
listen-port=6881-6999
dht-file-path=@DHT_FILE@
dht-file-path6=@DHT6_FILE@
bt-enable-lpd=false
enable-peer-exchange=true

bt-tracker=udp://tracker.opentrackr.org:1337/announce,udp://open.tracker.cl:1337/announce,udp://9.rarbg.com:2810/announce,udp://tracker.openbittorrent.com:6969/announce,udp://exodus.desync.com:6969/announce,udp://tracker.torrent.eu.org:451/announce,udp://open.demonii.com:1337/announce,udp://tracker.dler.org:6969/announce,udp://explodie.org:6969/announce,udp://tracker1.bt.moack.co.kr:80/announce,udp://open.stealth.si:80/announce,udp://tracker.moeking.me:6969/announce,udp://opentracker.i2p.rocks:6969/announce,udp://tracker.internetwarriors.net:1337/announce,udp://coppersurfer.tk:6969/announce,udp://tracker.cyberia.is:6969/announce,udp://tracker.bittor.pw:1337/announce,udp://tracker.tiny-vps.com:6969/announce,udp://bt1.archive.org:6969/announce,udp://bt2.archive.org:6969/announce,https://tracker.tamersunion.org:443/announce,https://tracker.gbitt.info:443/announce,https://tr.burnabyhighstar.com:443/announce,http://tracker.openbittorrent.com:80/announce,http://tracker.bt4g.com:2095/announce,http://bt.okmp3.ru:2710/announce,udp://tracker.qu.ax:6969/announce,udp://tracker.skyts.net:6969/announce,udp://tracker.0x7c0.com:6969/announce,udp://tracker.ducks.party:1984/announce,udp://tracker.renfei.net:8080/announce,udp://tracker.leech.ie:1337/announce,udp://tracker.loligirl.cn:1337/announce,udp://bt.dl0.cc:6969/announce,udp://bt3.nyaatracker.com:80/announce,udp://tr.bangumi.moe:6969/announce,udp://t.nyaatracker.com:80/announce

bt-save-metadata=true
bt-load-saved-metadata=true
bt-metadata-only=false
bt-max-peers=@BT_MAX_PEERS@
bt-force-encryption=false
bt-require-crypto=false
bt-detach-seed-only=true
bt-remove-unselected-file=true
seed-time=@SEED_TIME@
seed-ratio=@SEED_RATIO@

enable-rpc=true
rpc-listen-all=false
rpc-allow-origin-all=false
rpc-listen-port=@RPC_PORT@
rpc-secret=@RPC_SECRET@
rpc-save-upload-metadata=true

save-session=@SESSION_FILE@
save-session-interval=15
input-file=@SESSION_FILE@
force-save=false
auto-save-interval=15

allow-overwrite=false
auto-file-renaming=true
