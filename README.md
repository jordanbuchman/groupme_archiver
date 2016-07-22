# groupme_archiver

A web application to archive, catalog, and search the message histories of GroupMe groups. Requires PostgreSQL 9.5+. Tested with Python 3.5 only (let me know if you can get it to work on 2.7)

## Setup

1. Create the file ```~/.groupy.key``` containing your GroupMe API key. You can find your API key by going to the [GroupMe developer site](https://dev.groupme.com/) and clicking "Access Token" at the top right.  


2. Run ```pip install -r requirements.txt``` to install dependencies.


3. Install PostgreSQL 9.5 or greater. 


4. Run ```createdb database```, replacing database with the name of the database you want to use for the app.

## Running the app

Either

```python app.py database [username] [password]```

or

```python3 app.py database [username] [password]```




