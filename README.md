# abs-nyt-bestsellers

A python script to keep the NYT best sellers list in a collection up to date in audiobookshelf.

## Installation / Usage

Edit the variables at the top of the python file. You will need to change the NYT_API_KEY and ABS_COLLECTIONS. Use crontab to schedule the execution weekly.

The script will automatically add or remove the books or audiobooks that you have in your library to the collection that are also on the NYT best sellers list.

### NYT_API_KEY

Sign up for a free developer account and [NYTimes.com](https://developer.nytimes.com). You will need to add an app, and obtain an API key.

### Edit the ABS_COLLECTIONS

The first set of keys should match your library names. The script will fail if the libraries do not already exist. In the example below, I have a library named "books" and a library named "audiobooks".
The key to the array will be the name of the collection, and the array will be the list_name_encoded values from the NYT API.

```
ABS_COLLECTIONS = {
    "books": { # this is the name of the library in ABS - must exist or this will fail
        "NY Times Best Sellers": ["combined-print-and-e-book-fiction", "combined-print-and-e-book-nonfiction", "hardcover-fiction", "hardcover-nonfiction", "trade-fiction-paperback", "paperback-nonfiction","advice-how-to-and-miscellaneous","childrens-middle-grade-hardcover","series-books","young-adult-hardcover","audio-fiction","audio-nonfiction","business-books","mass-market-monthly","middle-grade-paperback-monthly","young-adult-paperback-monthly"],
    },
    "audiobooks": { # this is the name of the library in ABS - must exist or this will fail
        "NY Times Best Sellers": ["combined-print-and-e-book-fiction", "combined-print-and-e-book-nonfiction", "hardcover-fiction", "hardcover-nonfiction", "trade-fiction-paperback", "paperback-nonfiction","advice-how-to-and-miscellaneous","childrens-middle-grade-hardcover","series-books","young-adult-hardcover","audio-fiction","audio-nonfiction","business-books","mass-market-monthly","middle-grade-paperback-monthly","young-adult-paperback-monthly"],
    }
}
```

A complete list of list_name_encoded values are below:

```
"combined-print-and-e-book-fiction" 
"combined-print-and-e-book-nonfiction" 
"hardcover-fiction" 
"hardcover-nonfiction" 
"trade-fiction-paperback" 
"paperback-nonfiction" 
"advice-how-to-and-miscellaneous" 
"childrens-middle-grade-hardcover" 
"picture-books" 
"series-books" 
"young-adult-hardcover" 
"audio-fiction" 
"audio-nonfiction" 
"business-books" 
"graphic-books-and-manga" 
"mass-market-monthly" 
"middle-grade-paperback-monthly" 
"young-adult-paperback-monthly"
```

### Edit ABS_DB_PATH

This should be the full path to your abs SQLite database file.