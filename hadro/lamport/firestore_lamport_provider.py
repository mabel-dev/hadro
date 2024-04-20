from .simple_lamport_provider import SimpleLamportProvider


class FirestoreLamportProvider(SimpleLamportProvider):
    def __init__(self, collection_id, document_id):

        try:
            import firebase_admin
            from firebase_admin import credentials
            from firebase_admin import firestore
        except ImportError as err:  # pragma: no cover
            from hadro.exceptions import MissingDependencyError

            raise MissingDependencyError(err.name) from err

        # Initialize Firebase admin SDK
        cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(
            cred,
            {
                "projectId": "your-project-id",
            },
        )

        self.db = firestore.client()
        self.collection_id = collection_id
        self.document_id = document_id

    def get_and_increment_counter(self):
        """Atomically retrieve and increment the Lamport counter stored in Firestore."""
        doc_ref = self.db.collection(self.collection_id).document(self.document_id)

        # Transaction to increment the counter in Firestore atomically
        @firestore.transactional
        def update_in_transaction(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            current_value = snapshot.get("counter") if snapshot.exists else 0
            new_value = current_value + 1
            transaction.update(doc_ref, {"counter": new_value})
            return current_value

        return update_in_transaction(self.db.transaction())


# Usage in your application (specifying Firestore collection and document)
lamport_clock = FirestoreLamportProvider("lamport_counters", "my_counter")
print(lamport_clock.get_and_increment_counter())
