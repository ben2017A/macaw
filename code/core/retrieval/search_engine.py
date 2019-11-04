from abc import ABC, abstractmethod
from bs4 import BeautifulSoup
import subprocess
import pyndri
import os
import requests

from code.core.retrieval.doc import get_trec_doc, Document
from code.util.text_parser import html_to_clean_text


class Retrieval(ABC):
	@abstractmethod
	def __init__(self, params):
		"""
		An abstract class for retrieval models.

		Args:
			params(dict): A dict containing some mandatory and optional parameters. 'query_generation' and 'logger' are
			required for all retrieval models.
		"""
		self.params = params
		self.query_generation = self.params['query_generation']

	@abstractmethod
	def retrieve(self, query):
		"""
		This method should retrieve documents for the given query.

		Args:
			query(str): The query string.
		"""
		pass

	def get_results(self, conv_list):
		"""
		This method is the one that should be called. It simply calls the query generation model to generate a query
		from a conversation list and then runs the retrieval model and returns the results.
		Args:
			conv_list(list): List of util.msg.Message, each corresponding to a conversational message from / to the
			user. This list is in reverse order, meaning that the first elements is the last interaction made by user.

		Returns:
			A list of Documents retrieved by the search engine.
		"""
		query = self.query_generation.get_query(conv_list)
		self.params['logger'].info('New query: ' + query)
		return self.retrieve(query)


class Indri(Retrieval):
	def __init__(self, params):
		"""
		The Indri retrieval model. Indri is an open-source search engine implemented as part of the lemur project by
		UMass Amherst and CMU. Refer to http://lemurproject.org/indri.php for more information.
		The retrieval model used here is based on language modeling framework and retrieves documents using the query
		likelihood retrieval model [Ponte & Croft; SIGIR 1998] and Dirichlet prior smoothing [Zhai and Lafferty; SIGIR
		2001]. It is implemented using the Pyndri [Van Gysel et al.; ECIR 2017], which is a python interface to Indri.
		Refer to http://lemurproject.org/indri.php for more information on the Lemur toolkit.

		Args:
			params(dict): A dict containing some parameters. Here is the list of all required parameters:
			'indri_path': The path to the installed Indri toolkit.
			'index': The path to the Indri index constructed from the collection.
			'results_requested': The maximum number of requested documents for retrieval. If not given, it is set to 1.
			'text_format': The text format for document collection (e.g., 'trectext').
			Note that the parameters 'query_generation' and 'logger' are required by the parent class.
		"""
		super().__init__(params)
		self.results_requested = self.params['results_requested'] if 'results_requested' in self.params else 1
		self.indri_path = self.params['indri_path']
		self.index = pyndri.Index(self.params['index'])
		self.term2id, self.id2term, self.id2df = self.index.get_dictionary()
		self.id2tf = self.index.get_term_frequencies()

	def retrieve(self, query):
		"""
		This method retrieve documents in response to the given query.

		Args:
			query(str): The query string.

		Returns:
			A list of Documents with the maximum length of the 'results_requested' parameter.
		"""
		int_results = self.index.query(query, results_requested=self.results_requested)
		results = []
		for int_doc_id, score in int_results:
			# ext_doc_id, content_term_id = self.index.document(int_doc_id)
			# index_content = [self.id2term[term_id] if term_id> 0 else 'UNK' for term_id in content_term_id]
			doc = self.get_doc_from_index(int_doc_id)[0]
			doc.score = score
			doc.id = str(int_doc_id)
			results.append(doc)
		return results

	def get_doc_from_index(self, doc_id):
		"""
		This method retrieves a document content for a given document id.

		Args:
			doc_id(str): The document ID.

		Returns:
			A Document from the collection whose ID is equal to the given doc_id. For some reasons, the method returns
			a list of Documents with a length of 1.
		"""
		content = subprocess.run([os.path.join(self.indri_path, 'dumpindex/dumpindex'), self.params['index'],
								  'dt', str(doc_id)], stdout=subprocess.PIPE).stdout.decode('UTF-8')
		if self.params['text_format'] == 'trectext':
			doc = get_trec_doc(content)
		else:
			raise Exception('The requested text format is not supported!')
		return [doc]


class BingWebSearch(Retrieval):
	def __init__(self, params):
		"""
		The Microsoft Bing Web search API. This class uses the Bing's API to get the retrieval results from the Web.
		Note that for some reasons, the results returned by the Bing API are usually different from the Bing search
		(without API).

		Args:
			params(dict): A dict containing some parameters. Here is the list of all required parameters:
			'bing_key': The Bing API key.
			'results_requested': The maximum number of requested documents for retrieval. If not given, it is set to 1.
			Note that this is limited by the number of results returned by the API.
		"""
		super().__init__(params)
		self.results_requested = self.params['results_requested'] if 'results_requested' in self.params else 1
		self.subscription_key = self.params['bing_key']
		self.bing_api_url = 'https://api.cognitive.microsoft.com/bing/v7.0/search'
		self.header = {"Ocp-Apim-Subscription-Key": self.subscription_key}
		params['logger'].warning('There is a maximum number of transactions per second for the Bing API.')

	def retrieve(self, query):
		"""
		This method retrieve documents in response to the given query.

		Args:
			query(str): The query string.

		Returns:
			A list of Documents with the maximum length of the 'results_requested' parameter.
		"""
		params = {"q": query, "textDecorations": True, "textFormat": "HTML"}
		response = requests.get(self.bing_api_url, headers=self.header, params=params)
		response.raise_for_status()
		search_results = response.json()
		results = []
		for i in range(min(len(search_results['webPages']['value']), self.results_requested)):
			id = search_results['webPages']['value'][i]['url']
			title = search_results['webPages']['value'][i]['name']
			text = search_results['webPages']['value'][i]['snippet']
			text = ' '.join(BeautifulSoup(text, "html.parser").stripped_strings)
			headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 6.0; WOW64; rv:24.0) Gecko/20100101 Firefox/24.0'}
			text = html_to_clean_text(requests.get(id, headers=headers).content)
			score = 10 - i # this is not a score returned by Bing
			results.append(Document(id, title, text, score))
		return results

	def get_doc_from_index(self, doc_id):
		"""
		This method retrieves a document content for a given document id (i.e., URL).

		Args:
			doc_id(str): The document ID.

		Returns:
			A Document from the collection whose ID is equal to the given doc_id. For some reasons, the method returns
			a list of Documents with a length of 1.
		"""
		doc = Document(doc_id, doc_id, doc_id, -1)
		return [doc]
