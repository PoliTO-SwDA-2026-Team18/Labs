// MongoDB Playground
// Use Ctrl+Space inside a snippet or a string literal to trigger completions.

// The current database to use.
use("mzinga");

// Find a document in a collection.
db.getCollection("payload-preferences").deleteMany({
  key: "communications-list",
});
