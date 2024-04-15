# Data Storage

## Context

## Scope and Purpose

### Purpose of the Document

### Scope

Data Storage for log data and for reference data.

### Assumptions

For the purposes of this document, the below are assumed to be true:

- The solution will be cloud-hosted

### Requirements

- Unified access plane for platform data
- Fast searches
- Durable Data
- Auditable Data
- Protected Data
- Cost Effective
- Easy to Use
- Maintainable
- Dynamic and Adaptive

## Problem Space

- Terabytes of data will be stored
- Data should be available to readers in less than 10 seconds after being written
- Data should have a clear history and auditability
- Both streaming data and Snapshot data should be supported
- Searches through data should take no longer than 10 seconds per month of data being searched
- Time Travel required
- Exposure to other parts of the organization, is required
- Data must have dataset access restrictions
- Data is of varying data types

## Prior Art

This design is evolutionary leap on the current design of the underlying VA storage. This design addresses some of the challenges and includes some of the key learnings from operating that platform for four years.

Significant new functions are the Git-Like semantics which will retain data immutability with the capability to perform atomic updates - this has no current implementation, and creation of a file format designed for long-term storage; this is building on existing short-term storage development for in-memory storage and work done to emulate Firestore on GCS.

## Solution Overview

Container-based solution

Log Structured Merge Tree (LSM) & String Sorted Table (SST)
Vector Index - Supporting a Full-Text Search and a Similarity Search 

Data Catalogue

Metadata and Statistics

## Affected Components

### [NEW] Long-term Storage

#### [NEW] Format

A bespoke file format to meet the needs; this would be row-based in line with the LSM.

| Component    | Description                                                                    |
| ------------ | ------------------------------------------------------------------------------ |
| Magic Bytes  | File type marker and version string                                            |
| Data Block   | LZ4 compressed data block, data stored as packed byte representation of tuples |
| Schema       | Defines the structure of the data including column names and data types        |
| Key Block    | Key table, in key order, holding offset and length of record in Data Block     |
| Statistics   | Data statistics for prefiltering and query planning                            |
| Index Blocks | Blocks holding indexes, expected to be bitmap, sorted lists and vector tables  |
| Block Table  | Location, length, hash and type information for each block in the file         |
| Metadata     | File timestamp                                                                 |
| Magic Bytes  | Confirms the file is complete                                                  |

All blocks start with a length prefix, allowing the blocks to be read in chunks without needing to read the Block Table.

The simplest access scenario requires decoding the Data and Schema blocks only, this could be used to load the dataset into another format (such as Pandas or Arrow) without needing to support advanced access use cases.

**Data Block**

LZ4 compressed to reduce file size, MSGPACK encoded tuples, with special encoding for timestamps and other types not natively supported by MSGPACK. Each record is length prefixed to allow sequential loading without first reading the Key Block.

**Schema**

MSGPACKed serialization of JSON Object/Python Dictionary

**Key Block**

Records are fixed length to allow index-based random access.

Each key is the PK, the version (nanoseconds after Unix Epoch), the record length, the record offset

**Statistics**

Per column stats on Min/Max values, data distribution (Distogram), Cardinality Estimation (KMV) to support advanced access patterns.

**Index Blocks**

Bitmap Indexes used to index low cardinality columns.
Sorted Index used for binary and range searches.
Vector Index used for similarity searches and probabilistic filtering (Bloom Index).

**Block Table**

Holds information relating to each block:

- Type
- Offset
- Size
- Compression
- Hash

**Metadata**

Timestamp for the file

**End Of File**

#### [NEW] Infrastructure

Files will be partitioned by primary key, ensuring large datasets can be processed in parallel.

Files should be flushed to storage when reaching 10k tuples or when been open for 5 minutes, which ever is sooner.

### [NEW] Data Presentation Layer

A single presentation layer, with multiple interfaces:

- HTTP API is expected to be the primary interface
- ODBC support for other use cases - will front the same instance of the same engine as the HTTP API

Reads will include scanning the WAL (this is not like a 'normal' database)

The DQL will be SQL, some DQL and DML may need to be down using non-SQL commands.

It is expected this service will run in Kubernetes or some other long-running environment with access to local storage.

The Data Presentation Layer should support accessing datasets in other storage platforms (Splunk, BigQuery, Postgres, etc) as required as well.

### [NEW] Processes

To ensure read performance is efficient, regular compaction of SSTables will take place and an MVVC approach will be used to ensure reads are not interrupted during compaction. 

Data will be broadly classified into three groups which defines how compaction takes place:

- Log Data: there is no concept of a complete dataset for logs, compaction will reduce the number of fragments only
- State Data: where writes are updates to state, compaction will step back to the previous compaction and apply deltas
- Snapshot Data: where an entire dataset is written at-a-time so no compaction is needed

Compaction can be at any schedule appropriate to the volitity of the dataset in order to maintain good read speed.

Compacted files will not be removed, they will be retained in order to respond to AS AT TIMESTAMP queries, which may not be possible following compaction.

### [NEW] Data Management

Datasets will support basic Git functionality:

- FORK (make a copy of a dataset)
- PULL (sync a copy with this source)
- COMMIT (make changes to a dataset, make a branch or update a branch)
- MERGE (update one branch with updates from another)

Each event is tracked in a Merkle Tree, recording the hash, time, SSTable and agent. This provides a full audit trail of who changed what when.

This also allows for data to be recovered, by making a fork of known good position and using the fork as the main branch.

### [NEW] Data Catalogue

A data catalogue will be required to assist with discovery of data.

The data catalgue will store schema and permission information, allowing the Data Presentation Layer to allow and deny access to data based on the user.

### [NEW] Permissions

Two dimensions of permissions should be defined:

- Entitlements - aligning closer to traditional RBAC, where users have permissions to CRUD based on roles
- Visibility - contextual (AD groups, HR details) permissions, restricting what data is visible to specific users

## Requirement Mapping

**Unified access plane for platform data**

This is the core topic of this designed

**Fast searches**

Hot data caching
Parallel processable data
Indexed data, including a faux full-text index

**Durable Data**

Multi-region GCS

**Auditable Data**

Git-Like semantics for changes

**Protected Data**

Access controls around entitlements (CUD)
Access controls around visibility (R)

**Cost Effective**

Compression
Only paying Storage and Compute primitive costs

**Easy to Use**

Core user interface is SQL - low-code

**Maintainable**

In control of all parts

**Dynamic and Adaptive**

In control of all parts, can evolve as we need, not as others want

## Other Options

- BigQuery
- Splunk
- Mongo
- Neo4J

## Security Considerations

It is likely engineers on the platform will have some access to all data.

Building own permissions model is hard.

## Cost Considerations



## Integration with Other Initiatives

- Insights platform via ODBC
- Workbench via HTTP API
- Pipelines via HTTP API

## Suggested Delivery

Step | Step
---- | ----
1    | Agree file format
2.1  | Create file reader/writer
2.2  | Create HTTP Data Presentation Layer
2.3  | Create Permissions Model