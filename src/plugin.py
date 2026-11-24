# for localized messages
from . import _, group_titles

from hashlib import md5
from os import makedirs as os_makedirs, path as os_path, remove as os_remove
from re import search as re_search
from requests import get, exceptions
from shutil import rmtree
from time import time
from xml.sax.saxutils import escape as xml_escape

from enigma import eDVBDB

from Components.ActionMap import ActionMap
from Components.config import config, ConfigSelection, ConfigSubsection, getConfigListEntry
from Components.ConfigList import ConfigListScreen
from Components.Label import Label
from Components.Sources.StaticText import StaticText
from Plugins.Plugin import PluginDescriptor
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen

config.plugins.iptv_org = ConfigSubsection()
choices = {
	"country": _("Country"),
	"genre": _("Category"),
	"language": _("Language"),
}
config.plugins.iptv_org.current = ConfigSelection(default="country", choices=list(choices.items()))


def make_service_ref(tvg_id, url, channelname):
	stype = "5002" if os_path.exists("/usr/bin/exteplayer3") else "4097"
	key = (tvg_id or channelname).encode("utf-8", errors="ignore")
	digest = md5(key).hexdigest()
	sid = int(digest[0:4], 16) or 1
	tsid = int(digest[4:8], 16) or 1
	onid = int(digest[8:12], 16) or 1
	sref_epg = "%s:0:1:%X:%X:%X:CCCC0000:0:0:0:" % (stype, sid, tsid, onid)
	sref_bouquet = "%s%s:%s" % (sref_epg, url.replace(":", "%3a"), channelname)
	return sref_epg, sref_bouquet


def write_epgimport_files(channel_map):
	if not channel_map or not os_path.isdir("/etc/epgimport"):
		return
	channels_file = "/etc/epgimport/iptv-org.channels.xml"
	sources_file = "/etc/epgimport/iptv-org.sources.xml"

	existing = {}
	if os_path.exists(channels_file):
		try:
			with open(channels_file, "r", encoding="utf-8", errors="ignore") as f:
				for line in f:
					m = re_search(r'<channel id="([^"]+)">([^<]+)</channel>', line)
					if m:
						existing[m.group(1)] = m.group(2)
		except Exception:
			pass

	existing.update(channel_map)

	with open(channels_file, "w", encoding="utf-8") as f:
		f.write('<?xml version="1.0" encoding="utf-8"?>\n<channels>\n')
		for cid, sref in sorted(existing.items()):
			f.write('  <channel id="%s">%s</channel>\n' % (xml_escape(cid), xml_escape(sref)))
		f.write("</channels>\n")

	guides = [
		("Brazil (mi.tv)", "https://github.io"),
		("Brazil (Claro TV+)", "https://github.io"),
		("Portugal (MEO)", "https://github.io"),
		("USA (TVGuide)", "https://github.io"),
		("Spain (Movistar+)", "https://github.io"),
		("Latin America (gatotv)", "https://github.io"),
	]

	with open(sources_file, "w", encoding="utf-8") as f:
		f.write('<?xml version="1.0" encoding="utf-8"?>\n<sources>\n')
		f.write('  <sourcecat sourcecatname="IPTV-ORG Universal EPG">\n')
		for label, url in guides:
			f.write('    <source type="gen_xmltv" nocheck="1" channels="%s">\n' % channels_file)
			f.write("      <description>iptv-org - %s</description>\n" % xml_escape(label))
			f.write("      <url>%s</url>\n" % xml_escape(url))
			f.write("    </source>\n")
		f.write("  </sourcecat>\n</sources>\n")


class Fetcher():
	def __init__(self):
		self.bouquetName = _("iptv-org")
		self.bouquetFilename = "userbouquet.iptv_org.%s.tv"
		self.tempDir = "/tmp/iptv-org"
		self.playlists = {
			"country": "https://github.io",
			"genre": "https://github.io",
			"language": "https://github.io",
		}
		self.cachefile = "/tmp/iptv-org.cache"
		self.cache = {}
		if os_path.exists(self.cachefile):
			if time() - os_path.getmtime(self.cachefile) > 86400:
				try:
					os_remove(self.cachefile)
				except OSError:
					pass
			else:
				try:
					with open(self.cachefile, "r", encoding="utf-8") as f:
						self.cache = eval(f.read())
				except Exception:
					pass

	def downloadPage(self):
		os_makedirs(self.tempDir, exist_ok=True)
		link = self.playlists[config.plugins.iptv_org.current.value]
		try:
			response = get(link, timeout=(5.0, 15.0))
			response.raise_for_status()
			with open(self.tempDir + "/" + config.plugins.iptv_org.current.value, "wb") as f:
				f.write(response.content)
		except exceptions.RequestException as e:
			print("[iptv-org] Error downloading playlist:", e)

	def getPlaylist(self):
		if config.plugins.iptv_org.current.value not in self.cache:
			self.downloadPage()
			current = {}
			group_title = ""
			channelname = ""
			tvg_id = ""
			fpath = self.tempDir + "/" + config.plugins.iptv_org.current.value
			if not os_path.exists(fpath):
				return
			with open(fpath, encoding="utf-8", errors="ignore") as f:
				for line in f:
					if line.startswith("#EXTINF:"):
						group_title = ""
						channelname = ""
						tvg_id = ""
						m_grp = re_search(r'group-title="([^"]*)"', line)
						if m_grp:
							group_title = m_grp.group(1)
						m_id = re_search(r'tvg-id="([^"]*)"', line)
						if m_id:
							tvg_id = m_id.group(1).strip()
						if "," in line:
							channelname = line.rsplit(",", 1)[1].strip()
					elif line.startswith("http"):
						url = line.strip()
						if group_title and channelname and url:
							if group_title not in current:
								current[group_title] = []
							current[group_title].append((channelname, url, tvg_id))
			rmtree(self.tempDir, ignore_errors=True)
			if current:
				self.cache[config.plugins.iptv_org.current.value] = current
				try:
					with open(self.cachefile, "w", encoding="utf-8") as f:
						f.write(repr(self.cache))
				except Exception:
					pass

	def createBouquet(self, group_title):
		current = self.cache.get(config.plugins.iptv_org.current.value, {})
		bouquet_list = []
		epg_map = {}
		if group_title in current and current[group_title]:
			bouquet_list.append("1:64:0:0:0:0:0:0:0:0:%s" % group_titles.get(group_title, group_title))
			for item in sorted(current[group_title]):
				channelname = item[0]
				url = item[1]
				tvg_id = item[2] if len(item) > 2 else ""
				sref_epg, sref_bouquet = make_service_ref(tvg_id, url, channelname)
				bouquet_list.append(sref_bouquet)
				if tvg_id:
					epg_map[tvg_id] = sref_epg
		if bouquet_list:
			duplicated_translation = list(group_titles.values()).count(group_titles.get(group_title, group_title)) > 1
			eDVBDB.getInstance().addOrUpdateBouquet(
				self.bouquetName + (" - " + choices[config.plugins.iptv_org.current.value] if duplicated_translation else "") + " - " + group_titles.get(group_title, group_title),
				self.bouquetFilename % (sanitizeFilename(group_title).replace(" ", "_").strip().lower(),),
				bouquet_list,
				False,
			)
			write_epgimport_files(epg_map)


def sanitizeFilename(filename):
	return "".join(c for c in filename if c.isalnum() or c in (" ", ".", "_")).rstrip()


class IptvOrgPlaylists(ConfigListScreen, Screen, Fetcher):
	def __init__(self, session):
		Screen.__init__(self, session)
		Fetcher.__init__(self)
		self.skinName = ["IptvOrgPlaylists", "Setup"]
		self.setTitle(_("iptv-org playlists"))
		self["key_red"] = StaticText(_("Exit"))
		self["key_green"] = StaticText(_("Create bouquet"))
		self["key_yellow"] = StaticText(_("Delete cache"))
		self["description"] = Label("")
		self["actions"] = ActionMap(
			["SetupActions", "ColorActions"],
			{
				"cancel": self.keyCancel,
				"ok": self.keyGreen,
				"green": self.keyGreen,
				"yellow": self.keyYellow,
			},
			-2,
		)
		self.list = []
		ConfigListScreen.__init__(self, self.list, session=session, on_change=self.changedEntry)
		self.createSetup()

	def createSetup(self):
		self.getPlaylist()
		current = self.cache.get(config.plugins.iptv_org.current.value, {})
		group_choices = [(k, "%s (%d)" % (group_titles.get(k, k), len(v))) for k, v in sorted(current.items(), key=lambda x: group_titles.get(x[0], x[0]).lower())]
		if not group_choices:
			group_choices = [("", _("No playlists available"))]
		self.group_config = ConfigSelection(choices=group_choices)
		self.list = [
			getConfigListEntry(_("Sort playlists by"), config.plugins.iptv_org.current, _("Select whether to group playlists by country, category, or language.")),
			getConfigListEntry(_("Select playlist"), self.group_config, _("Press OK or Green to create/update the bouquet and EPGImport mapping.")),
		]
		self["config"].list = self.list

	def changedEntry(self):
		if self["config"].getCurrent()[1] == config.plugins.iptv_org.current:
			self.createSetup()

	def keyGreen(self):
		if self.group_config.value:
			self.createBouquet(self.group_config.value)
			self.session.open(MessageBox, _("Bouquet and EPGImport channels created for %s!") % group_titles.get(self.group_config.value, self.group_config.value), MessageBox.TYPE_INFO, timeout=5)

	def keyYellow(self):
		if os_path.exists(self.cachefile):
			try:
				os_remove(self.cachefile)
			except OSError:
				pass
		self.cache = {}
		self.createSetup()
		self.session.open(MessageBox, _("Cache cleared and playlists reloaded."), MessageBox.TYPE_INFO, timeout=3)


def main(session, **kwargs):
	session.open(IptvOrgPlaylists)


def Plugins(**kwargs):
	return [
		PluginDescriptor(
			name=_("iptv-org playlists"),
			description=_("Make IPTV bouquets with EPGImport support from ://github.com"),
			where=PluginDescriptor.WHERE_PLUGINMENU,
			fnc=main,
		)
	]
