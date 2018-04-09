# -*- coding: utf-8 -*-
#
# Author: Synrey Yee
#
# Created at: 03/24/2018
#
# Description: Training, evaluation and inference
#
# Last Modified at: 04/08/2018, by: Synrey Yee

'''
==========================================================================
  Copyright 2018 Xingyu Yi (Alias: Synrey Yee) All Rights Reserved.

  Licensed under the Apache License, Version 2.0 (the "License");
  you may not use this file except in compliance with the License.
  You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License.
==========================================================================
'''

from __future__ import absolute_import
from __future__ import print_function
from __future__ import division

from . import model_helper

import tensorflow as tf

import time
import os
import codecs


TAG_S = 0
TAG_B = 1
TAG_M = 2
TAG_E = 3


def train(hparams, model_creator):
  out_dir = hparams.out_dir
  num_train_steps = hparams.num_train_steps
  steps_per_stats = hparams.steps_per_stats
  steps_per_external_eval = hparams.steps_per_external_eval
  if not steps_per_external_eval:
    steps_per_external_eval = 10 * steps_per_stats

  train_model = model_helper.create_train_model(hparams, model_creator)
  eval_model = model_helper.create_eval_model(hparams, model_creator)

  eval_txt_file = "%s.%s" % (hparams.eval_prefix, "txt")
  eval_lb_file = "%s.%s" % (hparams.eval_prefix, "lb")
  eval_iterator_feed_dict = {
    eval_model.txt_file_placeholder : eval_txt_file,
    eval_model.lb_file_placeholder : eval_lb_file
  }

  model_dir = hparams.out_dir

  # TensorFlow model
  train_sess = tf.Session(graph = train_model.graph)
  eval_sess = tf.Session(graph = eval_model.graph)

  with train_model.graph.as_default():
    loaded_train_model, global_step = model_helper.create_or_load_model(
        train_model.model, model_dir, train_sess, "train", init = True)

  print("First evaluation:")
  evaluation(eval_model, model_dir, eval_sess,
      eval_iterator_feed_dict)

  print("# Initialize train iterator...")
  train_sess.run(train_model.iterator.initializer)

  process_time = 0.0
  while global_step < num_train_steps:
    # train a batch
    start_time = time.time()
    try:
      step_result = loaded_train_model.train(train_sess)
      process_time += time.time() - start_time
    except tf.errors.OutOfRangeError:
      # finish one epoch
      print(
          "# Finished an epoch, step %d. Perform evaluation" %
          global_step)

      # Save checkpoint
      loaded_train_model.saver.save(
          train_sess,
          os.path.join(out_dir, "segmentation.ckpt"),
          global_step = global_step)
      evaluation(eval_model, model_dir, eval_sess,
          eval_iterator_feed_dict, init = False)

      train_sess.run(train_model.iterator.initializer)
      continue

    _, train_loss, global_step, batch_size = step_result
    if global_step % steps_per_stats == 0:
      avg_time = process_time / steps_per_stats
      # print loss info
      print("[%d][loss]: %f, time per step: %.2fs" % (global_step,
          train_loss, avg_time))
      process_time = 0.0

    if global_step % steps_per_external_eval == 0:
      # Save checkpoint
      loaded_train_model.saver.save(
          train_sess,
          os.path.join(out_dir, "segmentation.ckpt"),
          global_step = global_step)

      print("External Evaluation:")
      evaluation(eval_model, model_dir, eval_sess,
          eval_iterator_feed_dict, init = False)


def evaluation(eval_model, model_dir, eval_sess,
    eval_iterator_feed_dict, init = True):
  with eval_model.graph.as_default():
    loaded_eval_model, global_step = model_helper.create_or_load_model(
        eval_model.model, model_dir, eval_sess, "eval", init)

  eval_sess.run(eval_model.iterator.initializer,
      feed_dict = eval_iterator_feed_dict)

  total_char_cnt = 0
  total_right_cnt = 0
  total_line = 0
  while True:
    try:
      batch_char_cnt, batch_right_cnt, batch_size = loaded_eval_model.eval(eval_sess)
      total_char_cnt += batch_char_cnt
      total_right_cnt += batch_right_cnt
      total_line += batch_size
    except tf.errors.OutOfRangeError:
      # finish the evaluation
      break

  precision = total_right_cnt / total_char_cnt
  print("Eval precision: %.3f, of total %d lines" % (precision, total_line))


def load_data(inference_input_file):
  """Load inference data."""
  inference_data = []
  with codecs.getreader("utf-8")(
      tf.gfile.GFile(inference_input_file, mode="rb")) as f:
    for line in f:
      line = line.strip()
      if line:
        inference_data.append(u' '.join(list(line)))

  return inference_data


def inference(ckpt, input_file, trans_file, hparams, model_creator):
  infer_model = model_helper.create_infer_model(hparams, model_creator)

  infer_sess = tf.Session(graph = infer_model.graph)
  with infer_model.graph.as_default():
    loaded_infer_model = model_helper.load_model(infer_model.model,
        ckpt, infer_sess, "infer", init = True)

  # Read data
  infer_data = load_data(input_file)
  infer_sess.run(
        infer_model.iterator.initializer,
        feed_dict = {
            infer_model.txt_placeholder: infer_data,
            infer_model.batch_size_placeholder: hparams.infer_batch_size
        })

  with codecs.getwriter("utf-8")(
        tf.gfile.GFile(trans_file, mode="wb")) as trans_f:
    while True:
      try:
        text_raw, decoded_tags, seq_lens = loaded_infer_model.infer(infer_sess)
      except tf.errors.OutOfRangeError:
        # finish the evaluation
        break

      assert len(text_raw) == len(decoded_tags)
      assert len(seq_lens) == len(decoded_tags)

      for text_line, tags_line, length in zip(text_raw, decoded_tags, seq_lens):
        text_line = text_line[ : length]
        tags_line = tags_line[ : length]
        newline = u""

        for char, tag in zip(text_line, tags_line):
          char = char.decode("utf-8")
          if tag == TAG_S or tag == TAG_B:
            newline += u' ' + char
          else:
            newline += char

        newline = newline.strip()
        trans_f.write(newline + u'\n')