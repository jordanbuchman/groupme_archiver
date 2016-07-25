from flask import Flask, request, render_template
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from datetime import datetime, timedelta
import arrow
import groupy
import schedule #mrhwick's fork on github
import threading
import time
import sys

group_jobs = []

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""

    if isinstance(obj, datetime):
        serial = obj.isoformat()
        return serial
    raise TypeError ("Type not serializable")

#http://codereview.stackexchange.com/a/37287
def verbose_timedelta(delta):
    d = delta.days
    h, s = divmod(delta.seconds, 3600)
    m, s = divmod(s, 60)
    labels = ['day', 'hour', 'minute', 'second']
    dhms = ['%s %s%s' % (i, lbl, 's' if i != 1 else '') for i, lbl in zip([d, h, m, s], labels)]
    for start in range(len(dhms)):
        if not dhms[start].startswith('0'):
            break
    for end in range(len(dhms)-1, -1, -1):
        if not dhms[end].startswith('0'):
            break
    return ', '.join(dhms[start:end+1])

app = Flask(__name__,static_url_path='/static')
#app.debug = True

if len(sys.argv) == 4:
  conn = psycopg2.connect(database=sys.argv[1],user=sys.argv[2], password=sys.argv[3])
  conn_args = {'database':sys.argv[1],'user':sys.argv[2], 'password':sys.argv[3]}
elif len(sys.argv) == 3:
  conn = psycopg2.connect(database=sys.argv[1],user=sys.argv[2])
  conn_args = {'database':sys.argv[1],'user':sys.argv[2]}
elif len(sys.argv) == 2:
  conn = psycopg2.connect(database=sys.argv[1])
  conn_args = {'database':sys.argv[1]}

cur = conn.cursor(cursor_factory=RealDictCursor)

cur.execute("""
  CREATE TABLE IF NOT EXISTS messages(
    id text PRIMARY KEY,
    name text,
    message text,
    attachments text[],
    avatar_url text,
    created_at timestamp with time zone,
    user_id text,
    group_id text,
    likes text[]
  )
""")

cur.execute("""
  CREATE TABLE IF NOT EXISTS members(
    user_id text,
    nickname text,
    avatar text,
    group_id text,
    UNIQUE (user_id, group_id)
  )
""")

cur.execute("""
  CREATE TABLE IF NOT EXISTS groups(
    name text,
    image_url text,
    description text,
    id text PRIMARY KEY
  )
""")


def member_avatar(id):
  cur.execute("SELECT avatar FROM members WHERE user_id = %s;", (id,))
  return cur.fetchone()['avatar']

def member_nickname(id):
  cur.execute("SELECT nickname FROM members WHERE user_id = %s;", (id,))
  return cur.fetchone()['nickname']

app.jinja_env.filters['member_avatar'] = member_avatar

app.jinja_env.filters['member_nickname'] = member_nickname

def handle_update_group(group_id, lock):
  with lock:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    group = groupy.Group.list().filter(id=group_id).first
    cur.execute("INSERT INTO groups VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET name = %s, image_url = %s, description = %s", (group.name, group.image_url, group.description, group.id, group.name, group.image_url, group.description))

    members = group.members()
    cur.executemany("INSERT INTO members VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, group_id) DO UPDATE SET nickname = %s, avatar = %s",
    [(member.user_id,
      member.nickname,
      member.image_url,
      group.id,
      member.nickname,
      member.image_url)
    for member in members])

    messages = group.messages()
    cur.execute("SELECT id FROM messages WHERE group_id=%s ORDER BY id DESC LIMIT 1;", (group_id,))
    try:
      latest_id = cur.fetchone()['id']
    except:
      latest_id = None
    x = 0
    while messages.older():
      try:
        while messages.iolder():
          x +=1
          print(group_id, x)
          if messages.filter(id=latest_id):
            b = True
          if b:
            break
        if b:
          break
      except:
        pass
    cur.executemany("INSERT INTO messages VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
    [(msg.id,
      msg.name,
      msg.text,
      [str(x) for x in msg.attachments],
      msg.avatar_url,
      msg.created_at,
      msg.user_id,
      group.id,
      msg.favorited_by)
    for msg in messages])
    conn.commit()

    print("FINISHED ", group_id)

def handle_update_group_async(group_id, lock):
  thread = threading.Thread(target=handle_update_group, args=(group_id,lock))
  thread.daemon = True
  thread.start()

cur.execute("SELECT id FROM groups;")
groups = cur.fetchall()
for group in groups:
  print(group)
  schedule.every(1).minutes.do(handle_update_group_async, group_id=group['id'], lock=threading.Lock())
  group_jobs.append(group['id'])
schedule.run_all()
schedule.run_continuously()

@app.route("/")
def index():
  groups = groupy.Group.list()
  return render_template("index.html", groups=zip([g.name for g in groups], [g.id for g in groups]))

@app.route("/add_group/<group_id>")
def add_group(group_id):
  group = groupy.Group.list().filter(id=group_id).first
  if not group:
    return render_template("layout.html", message="Error! Group ID not found."), 404
  if group_id in group_jobs:
    return render_template("layout.html", message="Error! Group already added.")
  schedule.every(1).minutes.do(handle_update_group_async, group_id=group_id, lock=threading.Lock())
  group_jobs.append(group_id)
  schedule.run_all()
  return render_template("layout.html", message="Fetching group history, please wait. <br> Number of messages: {0}. <br> Estimated time for processing: {1}.".format(group.message_count, verbose_timedelta(timedelta(seconds=group.message_count/100*1.1))))

@app.route("/groups/<group_id>/members/<member_id>")
def member(group_id,member_id):
  num = int(request.args.get('num') if 'num' in request.args else 10)

  offset = int(request.args.get('offset')) if 'offset' in request.args else 0

  query = request.args.get('query') if 'query' in request.args else ''

  msg_id = None

  date_a = None

  if 'date' in request.args:
    date_a = arrow.get(request.args.get('date'), 'MM/DD/YYYY')
    date = date_a.datetime
    cur.execute("SELECT id FROM messages WHERE user_id=%s AND group_id=%s AND message ILIKE  '%%' || %s || '%%' AND created_at >= %s ORDER BY id ASC LIMIT 1;", (member_id, group_id, query, date))
    msg_id = cur.fetchone()['id']

  if 'msg_id' in request.args or msg_id:
    if offset < 0:
      msg_id = msg_id if msg_id else int(request.args.get('msg_id'))
      cur.execute("SELECT id FROM messages WHERE user_id=%s AND group_id=%s AND id::bigint >= %s AND message ILIKE  '%%' || %s || '%%' ORDER BY id ASC OFFSET %s LIMIT 1;", (member_id, group_id, msg_id, query, -offset))
      try:
        msg_id = cur.fetchone()['id']
      except:
        pass
      offset=0
    msg_id = msg_id if msg_id else int(request.args.get('msg_id'))
    cur.execute("SELECT * FROM messages WHERE user_id = %s AND group_id = %s AND id::bigint <= %s AND message ILIKE  '%%' || %s || '%%' ORDER BY id DESC OFFSET %s LIMIT %s;", (member_id, group_id, msg_id, query, offset, num))
  else:
    offset = max(offset,0)
    cur.execute("SELECT * FROM messages WHERE user_id = %s AND group_id = %s AND message ILIKE  '%%' || %s || '%%' ORDER BY id DESC OFFSET %s LIMIT %s;", (member_id, group_id, query, offset, num))
  data = cur.fetchall()

  if not msg_id:
    try:
      msg_id = data[0]['id']
    except:
      pass

  cur.execute("SELECT * FROM members WHERE user_id = %s AND group_id=%s;", (member_id,group_id))
  print(cur.mogrify("SELECT * FROM members WHERE user_id = %s AND group_id=%s;", (member_id,group_id)))
  member = cur.fetchall()

  cur.execute("SELECT created_at, id FROM messages WHERE user_id = %s AND group_id=%s AND message ILIKE  '%%' || %s || '%%' ORDER BY created_at DESC LIMIT 1;", (member_id, group_id, query))
  end = cur.fetchone()
  end_date = arrow.get(end['created_at']).format('MM/DD/YYYY')
  end_id = end['id']

  cur.execute("SELECT created_at, id FROM messages WHERE user_id = %s AND group_id=%s AND message ILIKE  '%%' || %s || '%%' ORDER BY created_at ASC LIMIT 1;", (member_id, group_id, query))
  start = cur.fetchone()
  start_date = arrow.get(start['created_at']).format('MM/DD/YYYY')
  start_id = start['id']


  return render_template("member.html", messages=data[::-1],member=(member[0] if len(member) > 0 else {}),id=member_id, offset=offset, num=num, query=query, group_id=group_id, msg_id=msg_id, date=date_a.format('MM/DD/YYYY') if date_a else '', start_date=start_date, end_date=end_date, start_id=start_id, end_id=end_id)

@app.route("/groups/<group_id>/members")
def members(group_id):
  cur.execute("SELECT * FROM members WHERE group_id = %s;",(group_id,))
  members = cur.fetchall()
  return render_template("members.html", members=members)

@app.route("/groups")
def groups():
  cur.execute("SELECT * FROM groups;")
  groups = cur.fetchall()
  return render_template("groups.html", groups=groups)

@app.route("/groups/<group_id>")
def group(group_id):
  num = int(request.args.get('num') if 'num' in request.args else 10)

  offset = int(request.args.get('offset') if 'offset' in request.args else 0)

  query = request.args.get('query') if 'query' in request.args else ''

  msg_id = None

  date_a = None

  if 'date' in request.args:
    date_a = arrow.get(request.args.get('date'), 'MM/DD/YYYY')
    date = date_a.datetime
    cur.execute("SELECT id FROM messages WHERE group_id=%s AND message ILIKE  '%%' || %s || '%%' AND created_at >= %s ORDER BY id ASC LIMIT 1;", (group_id, query, date))
    msg_id = cur.fetchone()['id']

  if 'msg_id' in request.args or msg_id:
    if offset < 0:
      msg_id = msg_id if msg_id else int(request.args.get('msg_id'))
      cur.execute("SELECT id FROM messages WHERE group_id=%s AND id::bigint >= %s AND message ILIKE  '%%' || %s || '%%' ORDER BY id ASC OFFSET %s LIMIT 1;", (group_id, msg_id, query, -offset))
      try:
        msg_id = cur.fetchone()['id']
      except:
        pass
      offset=0
    msg_id = msg_id if msg_id else int(request.args.get('msg_id'))
    cur.execute("SELECT * FROM messages WHERE group_id=%s AND id::bigint <= %s AND message ILIKE  '%%' || %s || '%%' ORDER BY id DESC OFFSET %s LIMIT %s;", (group_id, msg_id, query, offset, num))
  else:
    offset = max(offset,0)
    cur.execute("SELECT * FROM messages WHERE group_id=%s AND message ILIKE  '%%' || %s || '%%' ORDER BY id DESC OFFSET %s LIMIT %s;", (group_id, query, offset, num))
  data = cur.fetchall()

  if not msg_id:
    try:
      msg_id = data[0]['id']
    except:
      pass

  cur.execute("SELECT * FROM groups WHERE id = %s;", (group_id,))
  group = cur.fetchone()

  cur.execute("SELECT created_at, id FROM messages WHERE group_id=%s AND message ILIKE  '%%' || %s || '%%' ORDER BY created_at DESC LIMIT 1;", (group_id, query))
  end = cur.fetchone()
  end_date = arrow.get(end['created_at']).format('MM/DD/YYYY')
  end_id = end['id']

  cur.execute("SELECT created_at, id FROM messages WHERE group_id=%s AND message ILIKE  '%%' || %s || '%%' ORDER BY created_at ASC LIMIT 1;", (group_id, query))
  start = cur.fetchone()
  start_date = arrow.get(start['created_at']).format('MM/DD/YYYY')
  start_id = start['id']


  return render_template("group.html", messages=data[::-1], group=group, group_id=group_id, offset=offset, num=num, query=query, msg_id=msg_id, date=date_a.format('MM/DD/YYYY') if date_a else '', start_date=start_date, end_date=end_date, start_id=start_id, end_id=end_id)

if __name__ == "__main__":
  app.run()
