#!/usr/bin/python
"""
    This file is part of SleekMigrate.

    SleekMigrate is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

    SleekMigrate is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with SleekMigrate; if not, write to the Free Software
    Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
"""

import logging
import sleekxmpp
from optparse import OptionParser
import xml.dom.minidom
from xml.etree import cElementTree as ET

import os
import sys
import time
import csv
import codecs
import glob

def getText(node):
  rc = ""
  for node in node.childNodes:
    if node.nodeType in [node.TEXT_NODE, node.CDATA_SECTION_NODE]:
      rc += node.data
  return rc

class Account(object):
    def __init__(self, jid, password):
        self.jid = jid
        self.password = password
        self.rosterEntries = []

    def host(self):
        return self.splitJid()[1]

    def user(self):
        return self.splitJid()[0]

    def splitJid(self):
        return self.jid.split("@")

    def getVcardElement(self):
        return self.vcardElement

    def getPrivateElements(self):
        return self.privateElements


class RosterEntry(object):
    def __init__(self, jid, groups, name, subscription):
        self.jid = jid
        self.groups = groups
        self.name = name
        self.subscription = subscription

class TigaseCSVExporter(object):
    def __init__(self, fileName):
        self.out = file(fileName, "w")

    def export(self, user):
        logging.info("Exporting account " + user.jid)
        w = csv.writer(self.out)
        for rosterEntry in user.rosterEntries:
            if len(rosterEntry.groups) == 0:
                rosterEntry.groups = ("")
            if rosterEntry.groups[0] is None:
                rosterEntry.groups = ("")
            if len(rosterEntry.groups) > 1:
                rosterEntry.groups = (rosterEntry.groups[0])
            for group in rosterEntry.groups:
                w.writerow([user.jid, user.password, rosterEntry.jid,
                            rosterEntry.name, rosterEntry.subscription, group])

    def finalise(self):
        self.out.close()

class XEP0227Exporter(object):
    def __init__(self, fileName):
        self.fileName = fileName
        self.element = ET.Element('server-data')
        self.element.set('xmlns','http://www.xmpp.org/extensions/xep-0227.html#ns')
        self.hostElements = {}

    def elementForHost(self, host):
        hostElement = self.hostElements.get(host, None)
        if hostElement is None:
            hostElement = ET.Element('host')
            hostElement.set('jid', host)
            self.hostElements[host] = hostElement
            self.element.append(hostElement)
        return hostElement

    def export(self, user):
        logging.info("Exporting account " + user.jid)
        userElement = ET.Element('user')
        userElement.set('name', user.user())
        userElement.set('password', user.password)
        rosterElement = ET.Element('{jabber:iq:roster}query')
        for rosterEntry in user.rosterEntries:
            itemElement = ET.Element('{jabber:iq:roster}item')
            itemElement.set('jid', rosterEntry.jid)
            if rosterEntry.name:
                itemElement.set('name', rosterEntry.name)
            itemElement.set('subscription', rosterEntry.subscription)
            for group in rosterEntry.groups:
                if group is not None:
                    groupElement = ET.Element('{jabber:iq:roster}group')
                    groupElement.text = group
                    itemElement.append(groupElement)
            rosterElement.append(itemElement)
        userElement.append(rosterElement)
        if user.vcardElement is not None:
            userElement.append(user.vcardElement)
        if len(user.privateElements) > 0:
            privateElement = ET.Element('{jabber:iq:private}query')
            for privateSubElement in user.privateElements:
                privateElement.append(privateSubElement)
            userElement.append(privateElement)

        self.elementForHost(user.host()).append(userElement)

    def finalise(self):
        ET.ElementTree(self.element).write(self.fileName)

class XMPPAccountExtractor(sleekxmpp.ClientXMPP):
    def __init__(self, jid, password, ssl=False, plugin_config = {}, plugin_whitelist=[]):
        sleekxmpp.ClientXMPP.__init__(self, jid, password, ssl, plugin_config, plugin_whitelist)
        logging.info("Logging in as %s" % self.jid)
        self.add_event_handler("session_start", self.start, threaded=True)
        self.add_event_handler("roster_update", self.receive_roster)
        self.account = Account(jid, password)
        self.rosterDone = False
        self.vcardDone = False
        self.privatesDone = False
        self.sessionOkay = False
        self.timeout = 30
        self.privatesToRequest = ("{exodus:prefs}exodus","{storage:bookmarks}storage", "{storage:rosternotes}storage", "{storage:metacontacts}storage")

    def start(self, event):
        self.sessionOkay = True
        self.getRoster()

        while not self.vcardDone or not self.rosterDone or not self.privatesDone:
            time.sleep(1)
        self.disconnect()



    def fetch_privates(self):
        self.account.privateElements = []
        for privateToRequest in self.privatesToRequest:
            id = self.getNewId()
            iq = self.makeIq(id)
            iq.attrib['type'] = "get"
            iqRequestElement = ET.Element("{jabber:iq:private}query")
            iq.append(iqRequestElement)
            iqRequestElement.append(ET.Element(privateToRequest))
            iqResult = self.send(iq, self.makeIq(id), self.timeout)
            if iqResult is not None:
                midResult = iqResult.find("{jabber:iq:private}query")
                if midResult is not None:
                    result = midResult.find(privateToRequest)
                    if result is not None:
                        self.account.privateElements.append(result)
        self.privatesDone = True

    def fetch_vcard(self):
        id = self.getNewId()
        iq = self.makeIq(id)
        iq.attrib['type'] = "get"
        vcardRequestElement = ET.Element("{vcard-temp}vCard")
        iq.append(vcardRequestElement)
        vcardResult = self.send(iq, self.makeIq(id), self.timeout)
        self.account.vcardElement = vcardResult.find("{vcard-temp}vCard")
        self.vcardDone = True
        self.fetch_privates()



    def receive_roster(self, event):
        for jid in event:
            self.account.rosterEntries.append(RosterEntry(jid, event[jid]['groups'], event[jid]['name'], event[jid]['subscription']))
        self.rosterDone = True
        self.fetch_vcard()

    def export_okay(self):
        return self.sessionOkay

    def getAccount(self):
        return self.account

def authDetailsFromOpenFireFile(filename, domain):
    """ Return a list of auth dicts
    """
    file = open(filename)
    document = xml.dom.minidom.parseString(file.read())
    file.close()
    users = document.getElementsByTagName("User")

    auths = []
    for user in users :
      auths.append({
        'jid': getText(user.getElementsByTagName("Username")[0]) + "@" + domain, 
        'pass': getText(user.getElementsByTagName("Password")[0])})
    return auths

def authDetailsFromFile(filename):
    """ Return a list of auth dicts
    """
    logging.warn("The import method isn't unicode-safe, yet")
    reader = csv.reader(open(filename, "rb"))
    auths = []
    for row in reader:
        auths.append({'jid':row[0],'pass':row[1]})
    return auths

def authDetailsFromJabberdUserDir(jabberdUserDir):
  ## First, sanity-check the jabberdUserDir
  # We expect its structure to look like:
  # jabberdUserDir/{domain}/{user}.xml

  # If the user passed us something with the {domain} component in it,
  # we should abort now and log an error message.
  if glob.glob(os.path.join(jabberdUserDir, '*.xml')):
    logging.error("The jabberd user data directory you passed in contains XML files in it. Instead, we expect a directory that contains files in the following format: {domain}/{user}.xml.")
    logging.error("For example, provide /var/lib/jabber/ not /var/lib/jabber/yourdomain.com/.")
    sys.exit(1)

  users = []
  for user_xml_file in glob.glob(os.path.join(jabberdUserDir, '*/*.xml')):
    base, domain, user_part = user_xml_file.rsplit('/', 2)
    username = user_part.rsplit('.xml', 1)[0]

    parsed = ET.parse(user_xml_file)
    try:
      password = parsed.find('//{jabber:iq:auth}password').text
    except AttributeError:
      logging.error("It seems that %s is not a valid jabberd14 XML file" % 
                    user_xml_file)
      logging.error("Skipping it...")
      continue
    users.append({'jid': username + '@' + domain,
                  'pass': password})

  logging.debug("Found this many users in the jabberd14 directory: %d" %
                len(users))

  return users

class JabberUserDirAccountExtractor(object):
  def __init__(self, base_path, user_password_list, exporter):
    self.base_path = base_path
    self.user_password_list = user_password_list
    self.exporter = exporter

  def process(self):
    # For each user:
    for jid_and_pass in self.user_password_list:
      # Create the Account instance
      jid = jid_and_pass['jid']
      password = jid_and_pass['pass']
      account = Account(jid, password)

      # Find the XML file
      user_xml_file = os.path.join(self.base_path, account.host(),
                                   account.user() + '.xml')
      parsed = ET.parse(user_xml_file)

      # Populate the account object's roster entries
      for roster_xml_item in parsed.findall('//{jabber:iq:roster}item'):
        jid = roster_xml_item.get('jid')
        name = roster_xml_item.get('name')
        subscription = roster_xml_item.get('subscription')
        group_names = []
        # get group names
        for group in roster_xml_item.findall('{jabber:iq:roster}group'):
          group_names.append(group.text)

        # toss it all into a RosterEntry...
        roster_entry = RosterEntry(jid, group_names, name, subscription)
        # ...and append it into the Account.
        account.rosterEntries.append(roster_entry)

      # Now, can we fill in the user's vcard too?
      vcardElement = parsed.find('{vcard-temp}vCard')
      account.vcardElement = vcardElement

      # set the privateElements, if there are any
      account.privateElements = parsed.find('{jabber:iq:private}query') or []

      # That's all there is to do for this Account object.
      # We can now pass it to the Exporter.
      self.exporter.export(account)

if __name__ == '__main__':
    #parse command line arguements
    optp = OptionParser()
    optp.add_option('-q','--quiet', help='set logging to ERROR', action='store_const', dest='loglevel', const=logging.ERROR, default=logging.INFO)
    optp.add_option('-d','--debug', help='set logging to DEBUG', action='store_const', dest='loglevel', const=logging.DEBUG, default=logging.INFO)
    optp.add_option('-v','--verbose', help='set logging to COMM', action='store_const', dest='loglevel', const=5, default=logging.INFO)
    optp.add_option("-e","--export-formatter", dest="exportFormatter",  type='choice', default="xep0227", choices=("xep0227","tigase"), help="formatter for exported data")
    optp.add_option('-s','--server', help='domain to export', dest='hostname', default=None)
    #optp.add_option("-c","--config", dest="configfile", default="config.xml", help="set config file to use")
    optp.add_option("-j","--jabberd-user-dir", dest="jabberdUserDir", default=None, help="path to a {domain}/{user}.xml files from jabberd14")
    optp.add_option("-f","--user-file", dest="userFile", default="users.csv", help="name of CSV uname/password pairs file")
    optp.add_option("-o","--openfire-user-file", dest="openFireUserFile", default="", help="name of the OpenFire user export XML file")
    opts,args = optp.parse_args()

    logging.basicConfig(level=opts.loglevel, format='%(levelname)-8s %(message)s')

    ### Exporter configuration is first because it sets up function state without
    ### having side-effects that could fail (like reading files from disk or going
    ### to the network).
    exporterType = opts.exportFormatter
    if exporterType == "xep0227":
        exporter =  XEP0227Exporter('227.xml')
    elif exporterType == "tigase":
        exporter = TigaseCSVExporter('out.txt')
    else:
        logging.error("Unexpected Exporter type %s." % exporterType)

    if len(opts.openFireUserFile) != 0 :
      logging.info("Loading OpenFire user export file: %s" % opts.openFireUserFile)
      authDetails = authDetailsFromOpenFireFile(opts.openFireUserFile, opts.hostname)
    elif opts.jabberdUserDir:
      logging.info("Loading user list from jabberd14 XML files at: %s" % opts.jabberdUserDir)
      authDetails = authDetailsFromJabberdUserDir(opts.jabberdUserDir)
    else :
      logging.info("Loading user file: %s" % opts.userFile)
      authDetails = authDetailsFromFile(opts.userFile)

    plugin_config = {}

    ### If we are in Jabber mode, do not speak XMPP over the network. Simply look in the directory of
    ### provided XML files, and create an export.
    if opts.jabberdUserDir:
      extractor = JabberUserDirAccountExtractor(opts.jabberdUserDir, authDetails, exporter)
      extractor.process()

    else:
      for auth in authDetails:
        extractor = XMPPAccountExtractor(auth['jid'], auth['pass'], plugin_config=plugin_config, plugin_whitelist=[])
        if opts.hostname is None:
            extractor.connect()
        else:
            extractor.connect((opts.hostname, 5222))
        extractor.process(threaded=False)
        if extractor.export_okay():
            exporter.export(extractor.getAccount())
    exporter.finalise()
